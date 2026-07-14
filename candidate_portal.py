"""
Candidate portal — account-based self-service behind electhouserepublicans.com/candidates.

Landing offers Register and Log in.
  Register: candidate gives name + email.
    - email on file  -> emailed confirmation link -> set username + password -> logged in
    - email NOT on file -> "contact Chris Maidment" message + Signal notice to the admin
  Log in: username + password (returning candidates).
  Profile (token-authed): see the info we have, edit it, upload a headshot/photos.
    Changes apply straight onto the candidate's own record (they are authenticated as that
    candidate); an intake_submissions row is also written for audit.

Stateless/token-based so it works cross-domain through the ctehr-website Node proxy.
Tokens are minted with the app's existing invite-token signer (injected at init).
"""

import os
import re
import json
import socket
import logging
from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

portal_bp = Blueprint('portal', __name__, url_prefix='/portal/api')

# Injected by init_candidate_portal()
get_db_connection = None
release_db_connection = None
upload_file_to_storage = None
send_email = None
make_token = None      # generate_invite_token(user_type, user_id) -> str
read_token = None      # verify_invite_token(token, max_age) -> (user_type, user_id) | None
log_activity = None

PORTAL_BASE = os.environ.get('PORTAL_BASE_URL', 'https://electhouserepublicans.com')
APP_URL = os.environ.get('APP_URL', 'https://nhcandidaterecruitment.com')  # for admin approval links
# SSO handoff to the campaign-website builder (sites.winthehouse.gop). A shared
# secret both apps hold signs a short-lived token so a logged-in candidate lands
# in the builder already authenticated. Distinct from the app's own token signer.
SITES_SSO_BASE = os.environ.get('SITES_BASE_URL', 'https://sites.winthehouse.gop')
SSO_SHARED_SECRET = os.environ.get('SSO_SHARED_SECRET', '')
SSO_SALT = 'ws-sso-token'
SSO_TTL = 300  # 5 min — the handoff is used immediately
# Same shared-secret handoff into the yard-sign location finder, which otherwise
# sits behind a site password for anyone arriving from outside.
YARDSIGNS_SSO_BASE = os.environ.get('YARDSIGNS_BASE_URL', 'https://yardsigns.winthehouse.gop')
YARDSIGNS_SSO_SALT = 'ys-sso-token'
ACCESS_TTL = 7 * 24 * 3600          # email confirmation link
SESSION_TTL = 12 * 3600             # logged-in session token
SIGNAL_CLI_HOST = os.environ.get('SIGNAL_CLI_HOST', '127.0.0.1')
SIGNAL_CLI_PORT = int(os.environ.get('SIGNAL_CLI_PORT', '7583'))
SIGNAL_BOT_NUMBER = os.environ.get('SIGNAL_BOT_NUMBER', '+16036191776')
SIGNAL_ADMIN_NUMBER = os.environ.get('SIGNAL_ADMIN_NUMBER', '+15405981130')
ALLOWED_PHOTO_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'}
MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_EXTRA_PHOTOS = 5
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
CONTACT_MSG = ("Looks like we don't have an email address for you. Please reach out to "
               "Chris Maidment — chris@electhouserepublicans.com — 540.598.1130 to gain access.")


def init_candidate_portal(db_get, db_release, storage_upload, email_send,
                          token_make, token_read, activity_log):
    global get_db_connection, release_db_connection, upload_file_to_storage, send_email
    global make_token, read_token, log_activity
    get_db_connection = db_get
    release_db_connection = db_release
    upload_file_to_storage = storage_upload
    send_email = email_send
    make_token = token_make
    read_token = token_read
    log_activity = activity_log
    try:
        _ensure_consult_table()
    except Exception:
        logger.exception("consult table ensure failed")


def _signal_notify(message):
    """Fire a Signal message to the admin via the signal-cli JSON-RPC daemon. Best-effort."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(8)
        s.connect((SIGNAL_CLI_HOST, SIGNAL_CLI_PORT))
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "send",
            "params": {"account": SIGNAL_BOT_NUMBER, "recipient": [SIGNAL_ADMIN_NUMBER],
                       "message": message},
            "id": 1,
        }) + "\n"
        s.sendall(payload.encode('utf-8'))
        s.recv(4096)
        s.close()
        return True
    except Exception as e:
        logger.error(f"Signal notify failed: {e}")
        return False


def _cid_from_session():
    """Read the Bearer session token -> candidate_id, or None."""
    auth = request.headers.get('Authorization', '')
    tok = auth[7:].strip() if auth.lower().startswith('bearer ') else (request.form.get('token') or '').strip()
    if not tok:
        return None
    res = read_token(tok, SESSION_TTL)
    if not res or res.get('type') != 'portal_session':
        return None
    return res.get('id')


def _prefill(cur, candidate_id):
    cur.execute("""
        SELECT first_name, last_name, phone1, phone2, address, city, zip,
               twitter_x, facebook, instagram, photo_url,
               COALESCE(NULLIF(email,''), email1) AS email, username,
               external_campaign_url, donate_url
        FROM candidates WHERE candidate_id = %s
    """, (candidate_id,))
    r = cur.fetchone()
    if not r:
        return None
    keys = ['first_name', 'last_name', 'phone1', 'phone2', 'address', 'city', 'zip',
            'twitter_x', 'facebook', 'instagram', 'photo_url', 'email', 'username',
            'website', 'donate_url']
    p = {k: (v or '') for k, v in zip(keys, r)}
    p['town'] = p.get('city', '') or ''  # default the district town to the candidate's city
    cur.execute("""SELECT district_code FROM candidate_election_status
                   WHERE candidate_id=%s AND election_year=2026 LIMIT 1""", (candidate_id,))
    row = cur.fetchone()
    if row:
        p['district_code'] = row[0]
    # Only a State Rep filing carries a real House district + town (county offices etc. don't).
    cur.execute("""SELECT district_code, town FROM filings
                   WHERE candidate_id=%s AND election_year=2026 AND office='State Representative' LIMIT 1""", (candidate_id,))
    row = cur.fetchone()
    if row:
        p.setdefault('district_code', row[0])
        if row[1]:
            p['town'] = row[1]
    return p


def _towns_list(cur):
    cur.execute("""
        SELECT DISTINCT ON (display) display, full_district_code FROM (
            SELECT CASE WHEN ward IS NOT NULL AND ward != 0
                        THEN town || ' Ward ' || ward ELSE town END AS display, full_district_code
            FROM districts) t ORDER BY display, full_district_code
    """)
    return [{'town': r[0], 'district_code': r[1]} for r in cur.fetchall()]


PORTAL_FROM = os.environ.get('PORTAL_FROM',
                             '"Committee to Elect House Republicans" <info@electhouserepublicans.com>')


def _send_access_link(cid, fn, email):
    token = make_token('portal_access', cid)
    link = f"{PORTAL_BASE}/candidates.html?token={token}"
    html = f"""<div style="font-family:Arial,sans-serif;font-size:15px;color:#222;line-height:1.6">
        <p>Hi {fn or 'there'},</p>
        <p>Here's your secure login link for your candidate profile with the Committee to Elect House
        Republicans. Click below to sign in and manage your information &mdash; no password needed.</p>
        <p style="margin:24px 0"><a href="{link}" style="background:#b91c1c;color:#fff;padding:14px 28px;
        border-radius:6px;text-decoration:none;font-weight:700">Sign in to my profile</a></p>
        <p style="font-size:13px;color:#444">Or paste this link into your browser:<br>
        <a href="{link}" style="color:#b91c1c;word-break:break-all">{link}</a></p>
        <p style="color:#666;font-size:13px">This link expires in 7 days. If you didn't request it, you can ignore this email.</p></div>"""
    send_email(email, "Your login link — CTEHR candidate profile", html,
               f"Your login link (no password needed): {link}\nThis link expires in 7 days.",
               source=PORTAL_FROM)


# Equivalence groups of given names so nicknames match their formal names
# (Joe/Joseph, Tammy/Tamara, ...). Overlaps across groups are fine — variant
# lookups union every group the name appears in, then surname must also agree.
_NAME_GROUPS = [
    {'joe', 'joseph', 'joey'}, {'tammy', 'tamara', 'tamra', 'tammie'},
    {'jeff', 'jeffrey', 'jeffery'}, {'jon', 'john', 'jonathan', 'johnny', 'jonny'},
    {'pam', 'pamela'}, {'dan', 'daniel', 'danny'}, {'dave', 'david'},
    {'mike', 'michael', 'mick', 'mickey'}, {'bob', 'robert', 'rob', 'bobby', 'robby'},
    {'bill', 'william', 'will', 'willie', 'billy', 'liam'}, {'tom', 'thomas', 'tommy'},
    {'jim', 'james', 'jimmy', 'jamie'}, {'rich', 'richard', 'rick', 'ricky', 'dick'},
    {'chris', 'christopher', 'christine', 'christina', 'chrissy', 'kris'},
    {'matt', 'matthew', 'matty'}, {'nate', 'nathan', 'nathaniel'}, {'tony', 'anthony'},
    {'ed', 'edward', 'eddie', 'ted', 'teddy', 'ned'},
    {'steve', 'steven', 'stephen', 'stevie'}, {'ken', 'kenneth', 'kenny'},
    {'greg', 'gregory'}, {'andy', 'andrew', 'drew'},
    {'ben', 'benjamin', 'benny', 'benji'}, {'sam', 'samuel', 'samantha', 'sammy'},
    {'nick', 'nicholas', 'nicky', 'nico'}, {'fred', 'frederick', 'freddie', 'fritz'},
    {'gene', 'eugene'},
    {'cathy', 'kathy', 'katherine', 'catherine', 'kate', 'katie', 'kathleen', 'katharine'},
    {'liz', 'elizabeth', 'beth', 'lizzie', 'betsy', 'eliza', 'libby'},
    {'sue', 'susan', 'susie', 'suzanne'}, {'debbie', 'deborah', 'debra', 'deb'},
    {'peggy', 'meg', 'maggie', 'margaret', 'marge', 'greta'},
    {'patty', 'patricia', 'pat', 'patrick', 'trish', 'tricia'}, {'sandy', 'sandra'},
    {'barb', 'barbara', 'barbra', 'babs'}, {'becky', 'rebecca', 'becca'},
    {'jenny', 'jennifer', 'jen', 'jenn'}, {'vicky', 'victoria', 'vicki'},
    {'val', 'valerie'}, {'ron', 'ronald', 'ronnie'}, {'don', 'donald', 'donnie'},
    {'larry', 'lawrence', 'laurence'}, {'terry', 'terrence', 'terence', 'theresa', 'teresa'},
    {'gerry', 'gerald', 'jerry', 'jerome'}, {'hank', 'henry', 'harry', 'harold'},
    {'walt', 'walter', 'wally'}, {'marty', 'martin'}, {'art', 'arthur', 'artie'},
    {'al', 'albert', 'alan', 'allen', 'allan', 'alvin'},
    {'alex', 'alexander', 'alexandra', 'alexandria', 'xander'},
    {'vince', 'vincent', 'vinny'}, {'cindy', 'cynthia'}, {'connie', 'constance'},
    {'gail', 'abigail'}, {'gabe', 'gabby', 'gabriel', 'gabrielle'},
    {'charlie', 'charles', 'chuck', 'chip', 'charlene', 'charlotte'},
    {'frank', 'francis', 'franklin', 'frances', 'fran'}, {'tim', 'timothy', 'timmy'},
    {'phil', 'philip', 'phillip'}, {'doug', 'douglas'}, {'ray', 'raymond'},
    {'stan', 'stanley'}, {'mitch', 'mitchell'}, {'brad', 'bradley', 'bradford'},
    {'curt', 'curtis', 'kurt'}, {'wes', 'wesley'}, {'cal', 'calvin'},
    {'lou', 'louis', 'louise', 'lewis'}, {'josh', 'joshua'}, {'zack', 'zachary', 'zach'},
    {'jess', 'jessica', 'jesse', 'jessie'}, {'angie', 'angela', 'angelina'},
    {'tina', 'christina', 'martina', 'valentina'}, {'kim', 'kimberly'},
    {'dot', 'dorothy', 'dottie'}, {'theo', 'theodore'}, {'manny', 'manuel', 'emmanuel'},
]
_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}


def _name_variants(first):
    """All given-name forms equivalent to `first` (itself plus any nickname group)."""
    first = (first or '').strip().lower()
    v = {first} if first else set()
    for g in _NAME_GROUPS:
        if first in g:
            v |= g
    return v


def _surname_tokens(s):
    """Surname split into comparable tokens, dropping apostrophes, hyphens, suffixes."""
    s = re.sub(r"['`’]", '', (s or '').lower())
    return set(t for t in re.split(r'[\s\-]+', s) if t and t not in _SUFFIXES)


def _match_by_name_town(cur, name, town):
    """Best-guess match of a registrant (name + town) to a candidate record, or None.

    Tolerant of nicknames (Joe/Joseph), compound/hyphenated surnames
    (Garthwaite vs. Simmons Garthwaite), name suffixes, and directional town
    prefixes (East Wakefield vs. Wakefield)."""
    toks = [p for p in re.split(r'\s+', (name or '').strip()) if p]
    if len(toks) < 2:
        return None
    reg_firsts = _name_variants(toks[0])
    reg_sur = _surname_tokens(' '.join(toks[1:]))
    if not reg_sur:
        return None

    cur.execute("SELECT candidate_id, first_name, last_name FROM candidates")
    ids = []
    for cid, cf, cl in cur.fetchall():
        cand_first = re.split(r'\s+', (cf or '').strip())
        cand_firsts = _name_variants(cand_first[0]) if cand_first else set()
        if not (reg_firsts & cand_firsts):
            continue
        if reg_sur & _surname_tokens(cl):   # any shared surname token
            ids.append(cid)
    ids = list(dict.fromkeys(ids))
    if len(ids) == 1:
        return ids[0]
    if not ids:
        return None

    if town:  # disambiguate multiple same-name people by the town's district
        town_variants = {town}
        town_variants.add(re.sub(r'^(east|west|north|south)\s+', '', town.strip(), flags=re.I))
        cur.execute("SELECT DISTINCT full_district_code FROM districts WHERE UPPER(town) = ANY(%s)",
                    ([t.upper() for t in town_variants],))
        tds = [r[0] for r in cur.fetchall()]
        if tds:
            cur.execute("""SELECT DISTINCT candidate_id FROM candidate_election_status
                           WHERE candidate_id = ANY(%s) AND district_code = ANY(%s)""", (ids, tds))
            pref = [r[0] for r in cur.fetchall()]
            if len(pref) == 1:
                return pref[0]
    return None


@portal_bp.route('/register-start', methods=['POST'])
def register_start():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()[:160]
    town = (data.get('town') or '').strip()[:120]
    phone = (data.get('phone') or '').strip()[:50]
    email = (data.get('email') or '').strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({'ok': False, 'error': 'Please enter a valid email address.'}), 400

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT candidate_id, first_name FROM candidates
                       WHERE LOWER(email)=%s OR LOWER(email1)=%s OR LOWER(email2)=%s
                       ORDER BY candidate_id LIMIT 1""", (email, email, email))
        row = cur.fetchone()
        if row:
            _send_access_link(row[0], row[1], email)
            return jsonify({'ok': True, 'found': True,
                            'message': f"We've emailed a secure access link to {email}."})

        # No email on file -> pending registration; admin approves from a Signal link.
        match_id = _match_by_name_town(cur, name, town)
        cur.execute("""INSERT INTO portal_registrations (name, town, email, phone, matched_candidate_id)
                       VALUES (%s,%s,%s,%s,%s) RETURNING id""", (name, town, email, phone, match_id))
        reg_id = cur.fetchone()[0]; conn.commit()
        atok = make_token('portal_reg', reg_id)
        approve_link = f"{APP_URL}/portal/api/approve?token={atok}"
        mtxt = f"\nAuto-matched to candidate #{match_id}." if match_id else "\nNo auto-match — review."
        _signal_notify("NEW candidate registration pending approval:\n"
                       f"{name} — {town} — {email}" + (f" — {phone}" if phone else "") + mtxt +
                       f"\n\nApprove & send login: {approve_link}")
        return jsonify({'ok': True, 'pending': True,
                        'message': "Your registration is pending approval — you'll receive an email once approved."})
    finally:
        cur.close(); release_db_connection(conn)


_APPROVE_PAGE = """<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{{font-family:Arial,sans-serif;max-width:520px;margin:56px auto;padding:0 20px;color:#222}}
.btn{{background:#b91c1c;color:#fff;border:0;padding:14px 28px;border-radius:6px;font-size:16px;font-weight:700;cursor:pointer}}
.box{{border:1px solid #e5e7eb;border-radius:10px;padding:24px}}</style></head>
<body><div class=box><h2>Candidate registration</h2>{body}</div></body></html>"""


@portal_bp.route('/approve', methods=['GET'])
def approve():
    res = read_token(request.args.get('token', ''), 14 * 24 * 3600)
    if not res or res.get('type') != 'portal_reg':
        return _APPROVE_PAGE.format(body="<p>This approval link is invalid or expired.</p>"), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT name,town,email,phone,matched_candidate_id,status FROM portal_registrations WHERE id=%s", (res['id'],))
        r = cur.fetchone()
        if not r:
            return _APPROVE_PAGE.format(body="<p>Registration not found.</p>"), 404
        name, town, email, phone, mid, status = r
        if status == 'approved':
            return _APPROVE_PAGE.format(body=f"<p>Already approved — {name} ({email}).</p>")
        cand = ''
        if mid:
            cur.execute("SELECT first_name,last_name FROM candidates WHERE candidate_id=%s", (mid,))
            c = cur.fetchone(); cand = f"{c[0]} {c[1]} (#{mid})" if c else f"#{mid}"
        body = (f"<p><b>{name}</b><br>{town}<br>{email}{(' &middot; '+phone) if phone else ''}</p>"
                + (f"<p>Approving sets this email/phone on <b>{cand}</b> and emails them the login link.</p>"
                   if mid else "<p style='color:#b91c1c'>No candidate auto-matched. Approving records the email; attach it to the right candidate in the recruitment app afterward.</p>")
                + f'<form method=post><input type=hidden name=token value="{request.args.get("token","")}">'
                  f'<button class=btn type=submit>Approve &amp; send login email</button></form>')
        return _APPROVE_PAGE.format(body=body)
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/approve', methods=['POST'])
def approve_do():
    res = read_token(request.form.get('token', ''), 14 * 24 * 3600)
    if not res or res.get('type') != 'portal_reg':
        return _APPROVE_PAGE.format(body="<p>This approval link is invalid or expired.</p>"), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT name,town,email,phone,matched_candidate_id,status FROM portal_registrations WHERE id=%s", (res['id'],))
        r = cur.fetchone()
        if not r:
            return _APPROVE_PAGE.format(body="<p>Registration not found.</p>"), 404
        name, town, email, phone, mid, status = r
        if status == 'approved':
            return _APPROVE_PAGE.format(body=f"<p>Already approved — {name} ({email}).</p>")
        if mid:
            cur.execute("""UPDATE candidates SET
                             email1 = CASE WHEN COALESCE(email1,'')='' THEN %s ELSE email1 END,
                             email2 = CASE WHEN COALESCE(email1,'')<>'' AND COALESCE(email2,'')='' THEN %s ELSE email2 END,
                             phone1 = CASE WHEN COALESCE(phone1,'')='' THEN %s ELSE phone1 END,
                             modified_by='portal-approval', modified_at=NOW()
                           WHERE candidate_id=%s""", (email, email, phone, mid))
            cur.execute("SELECT first_name FROM candidates WHERE candidate_id=%s", (mid,))
            fn = (cur.fetchone() or [''])[0]
            _send_access_link(mid, fn, email)
        cur.execute("UPDATE portal_registrations SET status='approved', approved_at=NOW(), approved_by='signal-admin' WHERE id=%s", (res['id'],))
        conn.commit()
        if log_activity and mid:
            log_activity('portal_registration_approved', f'Approved portal registration for {name} ({email})', mid)
        msg = (f"Approved. Login email sent to {email}." if mid
               else f"Recorded {email}. No candidate matched — attach it manually in the recruitment app.")
        return _APPROVE_PAGE.format(body=f"<p>{msg}</p>")
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/login-link', methods=['POST'])
def login_link():
    """Passwordless login: email on file -> email a one-click login link; unknown ->
    tell them to register. The single entry point for the unified login (post-cutover)."""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({'ok': False, 'error': 'Please enter a valid email address.'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT candidate_id, first_name FROM candidates
                       WHERE LOWER(email)=%s OR LOWER(email1)=%s OR LOWER(email2)=%s
                       ORDER BY candidate_id LIMIT 1""", (email, email, email))
        row = cur.fetchone()
    finally:
        cur.close(); release_db_connection(conn)
    if row:
        _send_access_link(row[0], row[1], email)
        return jsonify({'ok': True, 'sent': True,
                        'message': f"We've emailed a login link to {email}. Click it to sign in — no password needed."})
    return jsonify({'ok': True, 'sent': False, 'unknown': True,
                    'message': "We don't have that email on file. Register below and we'll get you set up."})


@portal_bp.route('/access', methods=['POST'])
def access():
    """Validate an emailed access token. Tells the front-end whether to show
    account setup (first time) or to issue a session (account already exists)."""
    data = request.get_json(silent=True) or {}
    tok = (data.get('token') or '').strip()
    res = read_token(tok, ACCESS_TTL) if tok else None
    if not res or res.get('type') != 'portal_access':
        return jsonify({'ok': False, 'error': 'This link is invalid or has expired. Please start over.'}), 400
    cid = res.get('id')
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT first_name FROM candidates WHERE candidate_id=%s", (cid,))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Account not found.'}), 404
        # Passwordless: the emailed access link IS the login. Issue a session directly —
        # no username/password setup step. (Password login via /login still works for
        # anyone who already set one.)
        cur.execute("UPDATE candidates SET last_login=NOW() WHERE candidate_id=%s", (cid,))
        conn.commit()
        return jsonify({'ok': True, 'needs_setup': False, 'session': make_token('portal_session', cid),
                        'first_name': row[0] or ''})
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/setup', methods=['POST'])
def setup():
    data = request.get_json(silent=True) or {}
    tok = (data.get('token') or '').strip()
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '')
    res = read_token(tok, ACCESS_TTL) if tok else None
    if not res or res.get('type') != 'portal_access':
        return jsonify({'ok': False, 'error': 'Your link expired. Please start over.'}), 400
    cid = res.get('id')
    if not re.match(r'^[A-Za-z0-9._-]{3,50}$', username):
        return jsonify({'ok': False, 'error': 'Username must be 3-50 characters (letters, numbers, . _ -).'}), 400
    if len(password) < 8:
        return jsonify({'ok': False, 'error': 'Password must be at least 8 characters.'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT candidate_id FROM candidates WHERE LOWER(username)=LOWER(%s) AND candidate_id<>%s",
                    (username, cid))
        if cur.fetchone():
            return jsonify({'ok': False, 'error': 'That username is taken. Please choose another.'}), 409
        cur.execute("""UPDATE candidates SET username=%s, password_hash=%s, password_changed=TRUE,
                       last_login=NOW(),
                       modified_by='candidate-portal', modified_at=NOW() WHERE candidate_id=%s""",
                    (username, generate_password_hash(password), cid))
        conn.commit()
        if log_activity:
            log_activity('portal_account_created', f'Candidate set up portal account (username {username})', cid)
        return jsonify({'ok': True, 'session': make_token('portal_session', cid)})
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '')
    if not username or not password:
        return jsonify({'ok': False, 'error': 'Enter your username or email, and your password.'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # Accept either a username or the email on file (candidates naturally type their email).
        cur.execute("""SELECT candidate_id, password_hash FROM candidates
                       WHERE LOWER(username) = LOWER(%s)
                          OR ((LOWER(email)=LOWER(%s) OR LOWER(email1)=LOWER(%s) OR LOWER(email2)=LOWER(%s))
                              AND COALESCE(password_hash,'') <> '')
                       ORDER BY CASE WHEN LOWER(username) = LOWER(%s) THEN 0 ELSE 1 END, candidate_id
                       LIMIT 1""", (username, username, username, username, username))
        row = cur.fetchone()
        if not row or not row[1] or not check_password_hash(row[1], password):
            return jsonify({'ok': False, 'error': 'Incorrect username or password.'}), 401
        cur.execute("UPDATE candidates SET last_login=NOW() WHERE candidate_id=%s", (row[0],))
        conn.commit()
        return jsonify({'ok': True, 'session': make_token('portal_session', row[0])})
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """Self-serve recovery: email the candidate a one-click login link (the same
    portal_access magic link used at signup). Reuses _send_access_link. Always
    returns a generic success so it can't be used to enumerate accounts. The link
    is sent to the email ON FILE, never to whatever the requester typed."""
    data = request.get_json(silent=True) or {}
    ident = (data.get('identifier') or data.get('email') or data.get('username') or '').strip()
    generic = jsonify({'ok': True,
        'message': "If that account exists, we just emailed a one-click login link to the address on file. Check your inbox (and spam)."})
    if not ident:
        return generic
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT candidate_id, first_name,
                              COALESCE(NULLIF(email,''), NULLIF(email1,''), NULLIF(email2,'')) AS email
                       FROM candidates
                       WHERE LOWER(username)=LOWER(%s) OR LOWER(email)=LOWER(%s)
                             OR LOWER(email1)=LOWER(%s) OR LOWER(email2)=LOWER(%s)
                       ORDER BY candidate_id LIMIT 1""", (ident, ident, ident, ident))
        row = cur.fetchone()
    finally:
        cur.close(); release_db_connection(conn)
    if row and row[2]:
        try:
            _send_access_link(row[0], row[1], row[2])
            if log_activity:
                log_activity('portal_password_reset_link', 'Emailed a login link (forgot password)', row[0])
        except Exception as e:
            logger.error(f"forgot-password email failed for candidate {row[0]}: {e}")
    return generic


@portal_bp.route('/profile', methods=['GET'])
def profile_get():
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    conn = get_db_connection(); cur = conn.cursor()
    try:
        p = _prefill(cur, cid)
        if p is None:
            return jsonify({'ok': False, 'error': 'Profile not found.'}), 404
        return jsonify({'ok': True, 'profile': p, 'towns': _towns_list(cur)})
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/profile', methods=['POST'])
def profile_post():
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401

    def field(name, maxlen=255):
        return (request.form.get(name) or '').strip()[:maxlen]

    sub = {'first_name': field('first_name', 100), 'last_name': field('last_name', 100),
           'phone1': field('phone1', 50), 'phone2': field('phone2', 50),
           'address': field('address'), 'city': field('city', 100), 'zip': field('zip', 20),
           'town': field('town', 100), 'district_code': field('district_code', 50),
           'facebook': field('facebook', 500), 'twitter_x': field('twitter_x', 500),
           'instagram': field('instagram', 500), 'website': field('website', 500),
           'donate_url': field('donate_url', 500),
           'notes': (request.form.get('notes') or '').strip()[:5000]}
    if not sub['first_name'] or not sub['last_name']:
        return jsonify({'ok': False, 'error': 'First and last name are required.'}), 400

    conn = get_db_connection(); cur = conn.cursor()
    try:
        # Photo uploads
        headshot_url = None; photo_urls = []
        files = []
        head = request.files.get('headshot')
        if head and head.filename:
            files.append(('headshot', head))
        for ph in request.files.getlist('photos')[:MAX_EXTRA_PHOTOS]:
            if ph and ph.filename:
                files.append(('photo', ph))
        for kind, f in files:
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            if ext not in ALLOWED_PHOTO_EXT:
                return jsonify({'ok': False, 'error': f'"{f.filename}" is not a supported image type.'}), 400
            f.seek(0, 2); size = f.tell(); f.seek(0)
            if size > MAX_PHOTO_BYTES:
                return jsonify({'ok': False, 'error': f'"{f.filename}" is over 15MB.'}), 400
            url = upload_file_to_storage(f, f"candidate_portal/{cid}/{secure_filename(f.filename)}")
            if not url:
                return jsonify({'ok': False, 'error': 'Photo upload failed. Please try again.'}), 500
            if kind == 'headshot':
                headshot_url = url
            else:
                photo_urls.append(url)

        # Apply directly onto the authenticated candidate's record (fill-or-update).
        cols = ['first_name', 'last_name', 'phone1', 'phone2', 'address', 'city', 'zip',
                'facebook', 'twitter_x', 'instagram']
        sets = [f"{c}=%s" for c in cols]
        vals = [sub[c] for c in cols]
        sets.append("external_campaign_url=%s"); vals.append(sub['website'])
        sets.append("donate_url=%s"); vals.append(sub['donate_url'])
        if headshot_url:
            sets.append("photo_url=%s"); vals.append(headshot_url)
        sets.append("modified_by='candidate-portal'"); sets.append("modified_at=NOW()")
        vals.append(cid)
        cur.execute(f"UPDATE candidates SET {', '.join(sets)} WHERE candidate_id=%s", vals)

        # Audit row
        cur.execute("""INSERT INTO intake_submissions
            (email, first_name, last_name, phone1, phone2, address, city, zip, town, district_code,
             facebook, twitter_x, instagram, website, notes, headshot_url, photo_urls,
             matched_candidate_id, auto_applied, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,'applied')""",
            ((_prefill(cur, cid) or {}).get('email', ''), sub['first_name'], sub['last_name'],
             sub['phone1'], sub['phone2'], sub['address'], sub['city'], sub['zip'], sub['town'],
             sub['district_code'], sub['facebook'], sub['twitter_x'], sub['instagram'], sub['website'],
             sub['notes'], headshot_url, json.dumps(photo_urls), cid))
        conn.commit()
        if log_activity:
            log_activity('portal_profile_update', 'Candidate updated their profile via the portal', cid)
        return jsonify({'ok': True, 'applied': True})
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/walkbook-request', methods=['GET'])
def walkbook_request_info():
    """Return the signed-in candidate's name + district so the form can prefill."""
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    conn = get_db_connection(); cur = conn.cursor()
    try:
        p = _prefill(cur, cid)
        if p is None:
            return jsonify({'ok': False, 'error': 'Profile not found.'}), 404
        # Most-recent pending request (so the form can say "already requested")
        cur.execute("""SELECT district_code, parties, status, created_at FROM walkbook_requests
                       WHERE candidate_id=%s ORDER BY id DESC LIMIT 1""", (cid,))
        last = cur.fetchone()
        return jsonify({'ok': True,
                        'first_name': p.get('first_name', ''), 'last_name': p.get('last_name', ''),
                        'district_code': p.get('district_code', ''), 'town': p.get('town', ''),
                        'last_request': ({'district_code': last[0], 'parties': last[1],
                                          'status': last[2], 'created_at': last[3].isoformat() if last[3] else None}
                                         if last else None)})
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/walkbook-request', methods=['POST'])
def walkbook_request_create():
    """A logged-in candidate requests a walkbook for their district. Saves the
    request and pings the admin on Signal (who builds it in the CRM)."""
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    data = request.get_json(silent=True) or {}

    parties = [p for p in (data.get('parties') or []) if p in ('REP', 'UND', 'DEM')]
    if not parties:
        parties = ['REP', 'UND']
    try:
        size = int(data.get('size') or 100)
    except (TypeError, ValueError):
        size = 100
    size = max(25, min(300, size))
    notes = (data.get('notes') or '').strip()[:2000]

    # Running mate(s) / teammates to share the walkbook with: [{name, email}]
    _email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    teammates = []
    for t in (data.get('teammates') or [])[:8]:
        if not isinstance(t, dict):
            continue
        tem = (t.get('email') or '').strip().lower()
        tnm = (t.get('name') or '').strip()[:120]
        if tem and _email_re.match(tem):
            teammates.append({'name': tnm, 'email': tem})

    conn = get_db_connection(); cur = conn.cursor()
    try:
        p = _prefill(cur, cid) or {}
        # District always comes from their profile (not trusted from the client)
        district = (p.get('district_code') or '').strip()
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        email = p.get('email', '')
        if not district:
            return jsonify({'ok': False, 'error':
                "We don't have a State House district on your profile yet. Add your town on your "
                "profile page first (it sets your district), then request your walkbook."}), 400

        cur.execute("""INSERT INTO walkbook_requests
            (candidate_id, candidate_name, email, district_code, parties, book_size, notes, status, created_at, teammates)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'new',NOW(),%s) RETURNING id""",
            (cid, name, email, district, ','.join(parties), size, notes,
             json.dumps(teammates) if teammates else None))
        rid = cur.fetchone()[0]
        conn.commit()
        if log_activity:
            log_activity('walkbook_requested', f'Requested a walkbook for {district}', cid)

        team_line = ""
        if teammates:
            team_line = "Also for: " + ", ".join(
                f"{t['name']} <{t['email']}>" if t['name'] else t['email'] for t in teammates) + "\n"
        _signal_notify(
            f"\U0001F6B6 NEW walkbook request #{rid}\n"
            f"{name} — {district}\n"
            f"Voters: {' + '.join(parties)}  ·  ~{size}/book\n"
            + team_line
            + (f"Notes: {notes}\n" if notes else "")
            + f"Email: {email}\n\n"
            "Reply 'walkbook requests' to see/manage all pending."
        )
        return jsonify({'ok': True,
                        'message': "Your walkbook request is in! We'll build it and email you when it's ready to canvass."})
    finally:
        cur.close(); release_db_connection(conn)


CRM_DATABASE_URL = os.environ.get('CRM_DATABASE_URL', '')
# Voter-list export — same backend the Signal !voterlist command uses.
VOTER_FILE_API = os.environ.get('VOTER_FILE_API', '')
VOTER_FILE_API_KEY = os.environ.get('VOTER_FILE_API_KEY', '')
VOTER_EXPORT_BUCKET = os.environ.get('VOTER_EXPORT_BUCKET', 'nhhouse-voter-lists')
VOTER_LINK_TTL = int(os.environ.get('VOTER_LINK_TTL', str(14 * 24 * 3600)))  # 14-day link for candidates


def _build_voterlist_csv(district, parties):
    """Pull a voter CSV for a House district + party mix via VOTER_FILE_API (the same
    /api/export the !voterlist Signal command uses). Loops parties (API takes one at a
    time) and concatenates. Returns (path, total) or (None, 0)."""
    import urllib.request, urllib.parse, tempfile, csv as _csv
    if not (VOTER_FILE_API and VOTER_FILE_API_KEY and district):
        return None, 0
    fd, path = tempfile.mkstemp(suffix='.csv', prefix='voterlist_')
    total = 0
    try:
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as out:
            writer = None
            for party in (parties or ['REP', 'UND']):
                qs = urllib.parse.urlencode({'district': district, 'party': party, 'format': 'csv'})
                req = urllib.request.Request(f"{VOTER_FILE_API}/api/export?{qs}",
                                             headers={'X-API-Key': VOTER_FILE_API_KEY})
                resp = urllib.request.urlopen(req, timeout=600)
                text = resp.read().decode('utf-8', 'replace').splitlines()
                resp.close()
                if not text:
                    continue
                header, rows = text[0], text[1:]
                if writer is None:
                    out.write(header + '\n')
                    writer = True
                for r in rows:
                    if r.strip():
                        out.write(r + '\n'); total += 1
        if total == 0:
            os.path.exists(path) and os.remove(path)
            return None, 0
        return path, total
    except Exception:
        logger.exception("voterlist CSV build failed")
        try:
            os.path.exists(path) and os.remove(path)
        except OSError:
            pass
        return None, 0


def _has_walkbooks(emails):
    """True if any of these emails already owns/was assigned a walkbook in the
    Action Center (nh_civic_crm). Best-effort, cross-DB, never raises."""
    emails = [e.strip().lower() for e in emails if e and e.strip()]
    if not emails or not CRM_DATABASE_URL:
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(CRM_DATABASE_URL)
        try:
            cur = conn.cursor()
            cur.execute("""SELECT 1 FROM users u
                           WHERE LOWER(u.email) = ANY(%s)
                             AND (EXISTS (SELECT 1 FROM walkbooks w WHERE w.assigned_to_id = u.id)
                               OR EXISTS (SELECT 1 FROM walkbook_assignments wa WHERE wa.user_id = u.id))
                           LIMIT 1""", (emails,))
            return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception:
        logger.exception("walkbook-status CRM check failed")
        return False


@portal_bp.route('/walkbook-status', methods=['GET'])
def walkbook_status():
    """Does this candidate already have walkbooks in the canvassing app? Drives the
    'Access your Walkbooks' button on the hub."""
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False}), 401
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT LOWER(email), LOWER(email1), LOWER(email2) FROM candidates WHERE candidate_id=%s", (cid,))
        row = cur.fetchone() or (None, None, None)
    finally:
        cur.close(); release_db_connection(conn)
    return jsonify({'ok': True, 'has_walkbooks': _has_walkbooks(list(row)),
                    'url': 'https://walkbooks.winthehouse.gop'})


@portal_bp.route('/voterlist-request', methods=['GET'])
def voterlist_request_info():
    """Prefill the voter-list request form with the candidate's name + district."""
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    conn = get_db_connection(); cur = conn.cursor()
    try:
        p = _prefill(cur, cid)
        if p is None:
            return jsonify({'ok': False, 'error': 'Profile not found.'}), 404
        cur.execute("""SELECT district_code, parties, status, created_at FROM voterlist_requests
                       WHERE candidate_id=%s ORDER BY id DESC LIMIT 1""", (cid,))
        last = cur.fetchone()
        return jsonify({'ok': True,
                        'first_name': p.get('first_name', ''), 'last_name': p.get('last_name', ''),
                        'district_code': p.get('district_code', ''), 'town': p.get('town', ''),
                        'last_request': ({'district_code': last[0], 'parties': last[1],
                                          'status': last[2], 'created_at': last[3].isoformat() if last[3] else None}
                                         if last else None)})
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/voterlist-request', methods=['POST'])
def voterlist_request_create():
    """A logged-in candidate requests a voter-list CSV for their district. Saves the
    request and pings the admin on Signal (who approves, pulls the list, and emails
    the candidate the CSV download link)."""
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    data = request.get_json(silent=True) or {}
    parties = [p for p in (data.get('parties') or []) if p in ('REP', 'UND', 'DEM')]
    if not parties:
        parties = ['REP', 'UND']
    notes = (data.get('notes') or '').strip()[:2000]
    conn = get_db_connection(); cur = conn.cursor()
    try:
        p = _prefill(cur, cid) or {}
        district = (p.get('district_code') or '').strip()
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        email = p.get('email', '')
        if not district:
            return jsonify({'ok': False, 'error':
                "We don't have a State House district on your profile yet. Add your town on your "
                "profile page first (it sets your district), then request your voter list."}), 400
        cur.execute("""INSERT INTO voterlist_requests
            (candidate_id, candidate_name, email, district_code, parties, notes, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,'new',NOW()) RETURNING id""",
            (cid, name, email, district, ','.join(parties), notes))
        rid = cur.fetchone()[0]
        conn.commit()
        if log_activity:
            log_activity('voterlist_requested', f'Requested a voter list for {district}', cid)
        approve_link = f"{APP_URL}/portal/api/voterlist/approve?token={make_token('voterlist_approve', rid)}"
        _signal_notify(
            f"\U0001F5F3️ NEW voter-list request #{rid}\n"
            f"{name} — {district}\n"
            f"Voters: {' + '.join(parties)}\n"
            + (f"Notes: {notes}\n" if notes else "")
            + f"Email: {email}\n\n"
            f"Approve & email the CSV: {approve_link}"
        )
        return jsonify({'ok': True,
                        'message': "Your voter-list request is in! Once it's approved we'll email you a link to download your CSV."})
    finally:
        cur.close(); release_db_connection(conn)


def _voterlist_fulfill_worker(rid, name, email, district, parties):
    """Build the CSV, host it, email the candidate the link. Runs in a background
    thread — a big district is far too slow for a web request."""
    path, total = _build_voterlist_csv(district, parties)
    if not path:
        logger.error("voterlist #%s: build failed for %s (%s)", rid, district, parties)
        try:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("UPDATE voterlist_requests SET status='failed' WHERE id=%s", (rid,)); conn.commit()
            cur.close(); release_db_connection(conn)
        except Exception:
            logger.exception("voterlist #%s: failed-status update errored", rid)
        return
    try:
        import boto3, datetime as _d
        stamp = _d.datetime.now(_d.timezone.utc).strftime('%Y-%m-%d')
        safe = re.sub(r'[^A-Za-z0-9]+', '', district or 'VoterList') or 'VoterList'
        key = f"voter-exports/{safe}_{'-'.join(parties)}_{stamp}.csv"
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        s3.upload_file(path, VOTER_EXPORT_BUCKET, key, ExtraArgs={'ContentType': 'text/csv'})
        link = s3.generate_presigned_url('get_object',
                    Params={'Bucket': VOTER_EXPORT_BUCKET, 'Key': key}, ExpiresIn=VOTER_LINK_TTL)
    except Exception:
        logger.exception("voterlist #%s upload failed", rid)
        return
    finally:
        try:
            os.path.exists(path) and os.remove(path)
        except OSError:
            pass

    fn = (name or 'there').split(' ')[0]
    html = (f'<div style="font-family:Arial,sans-serif;font-size:15px;color:#222;line-height:1.6">'
            f'<p>Hi {fn},</p>'
            f'<p>Here\'s the voter list you requested for <b>{district}</b> ({", ".join(parties)}) — '
            f'{total:,} voters. Click below to download the CSV:</p>'
            f'<p style="margin:22px 0"><a href="{link}" style="background:#b91c1c;color:#fff;padding:13px 26px;'
            f'border-radius:6px;text-decoration:none;font-weight:700">Download my voter list (CSV)</a></p>'
            f'<p style="font-size:13px;color:#666">This download link works for 14 days. Keep this list secure — '
            f'it\'s for your campaign\'s use.</p></div>')
    send_email(email, f"Your voter list — {district}", html,
               f"Your voter list for {district} ({', '.join(parties)}), {total} voters:\n{link}\n\nThis link works for 14 days.",
               source=PORTAL_FROM)
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("UPDATE voterlist_requests SET status='fulfilled', csv_url=%s, fulfilled_at=NOW() WHERE id=%s", (link, rid))
        conn.commit(); cur.close(); release_db_connection(conn)
    except Exception:
        logger.exception("voterlist #%s: fulfilled-status update errored", rid)
    if log_activity:
        log_activity('voterlist_fulfilled', f'Voter list for {district} emailed to {email}', None)


@portal_bp.route('/voterlist/approve', methods=['GET'])
def voterlist_approve():
    """Admin taps the approve link from the Signal message -> kicks off a background
    build (same export API as !voterlist) that emails the candidate the CSV link."""
    import threading
    res = read_token(request.args.get('token', ''), 30 * 24 * 3600)
    if not res or res.get('type') != 'voterlist_approve':
        return _APPROVE_PAGE.format(body="<p>This approval link is invalid or expired.</p>"), 400
    rid = res.get('id')
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT candidate_name, email, district_code, parties, status FROM voterlist_requests WHERE id=%s", (rid,))
        r = cur.fetchone()
        if not r:
            return _APPROVE_PAGE.format(body="<p>Request not found.</p>"), 404
        name, email, district, parties_s, status = r
        if status == 'fulfilled':
            return _APPROVE_PAGE.format(body=f"<p>Already sent — {name} ({email}) got their voter list.</p>")
        parties = [p for p in (parties_s or '').split(',') if p]
        if not email:
            return _APPROVE_PAGE.format(body=f"<p>No email on file for {name} — can't send the list.</p>"), 400
        cur.execute("UPDATE voterlist_requests SET status='processing' WHERE id=%s", (rid,)); conn.commit()
    finally:
        cur.close(); release_db_connection(conn)

    threading.Thread(target=_voterlist_fulfill_worker,
                     args=(rid, name, email, district, parties), daemon=True).start()
    return _APPROVE_PAGE.format(body=f"<p><b>On it.</b> Building the voter list for <b>{district}</b> "
                                     f"({', '.join(parties)}) and emailing it to {name} ({email}). "
                                     f"A big district takes a minute — you can close this page.</p>")


# ===========================================================================
# Consult booking — a logged-in candidate requests a short video consult with
# Chris. Approve-first: candidate picks an open slot -> admin approves -> a
# Google Calendar event with a Meet link is created and the candidate invited.
# Availability + event creation live in google_calendar.py.
# ===========================================================================
import datetime as _dt
try:
    import google_calendar as gcal
except Exception as _e:  # pragma: no cover
    gcal = None
    logger.warning("google_calendar module unavailable; consult booking disabled: %s", _e)

CONSULT_ADMIN_EMAIL = os.environ.get('CONSULT_ADMIN_EMAIL', 'chris@electhouserepublicans.com')


def _ensure_consult_table():
    if get_db_connection is None:
        return
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS consult_requests (
            id SERIAL PRIMARY KEY,
            candidate_id INTEGER,
            candidate_name TEXT,
            email TEXT,
            duration_min INTEGER,
            requested_start TIMESTAMPTZ,
            topic TEXT,
            status TEXT DEFAULT 'pending',
            meet_link TEXT,
            event_id TEXT,
            html_link TEXT,
            admin_note TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            decided_at TIMESTAMPTZ
        )""")
        conn.commit()
    except Exception:
        conn.rollback(); logger.exception("consult table init failed")
    finally:
        cur.close(); release_db_connection(conn)


def _fmt_when(start_iso):
    try:
        d = _dt.datetime.fromisoformat(start_iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=gcal.TZ)
        return d.astimezone(gcal.TZ).strftime('%a, %b %-d · %-I:%M %p ET')
    except Exception:
        return start_iso


def _pending_busy(cur):
    """Slots already held by pending/approved consults, as (start, end) intervals."""
    cur.execute("""SELECT requested_start, duration_min FROM consult_requests
                   WHERE status IN ('pending','approved') AND requested_start > NOW()""")
    out = []
    for start, dur in cur.fetchall():
        out.append((start, start + _dt.timedelta(minutes=int(dur or 30))))
    return out


@portal_bp.route('/consult', methods=['GET'])
def consult_info():
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    conn = get_db_connection(); cur = conn.cursor()
    try:
        p = _prefill(cur, cid) or {}
        cur.execute("""SELECT id, duration_min, requested_start, status, meet_link, created_at
                       FROM consult_requests WHERE candidate_id=%s ORDER BY id DESC LIMIT 1""", (cid,))
        r = cur.fetchone()
        last = None
        if r:
            is_past = bool(r[2] and r[2] < _dt.datetime.now(_dt.timezone.utc))
            last = {'id': r[0], 'duration_min': r[1],
                    'requested_start': r[2].isoformat() if r[2] else None,
                    'status': r[3], 'meet_link': r[4], 'is_past': is_past,
                    'created_at': r[5].isoformat() if r[5] else None}
        return jsonify({'ok': True,
                        'connected': bool(gcal and gcal.is_configured()),
                        'durations': list(gcal.ALLOWED_DURATIONS) if gcal else [15, 30],
                        'first_name': p.get('first_name', ''),
                        'has_email': bool(p.get('email')),
                        'last_request': last})
    finally:
        cur.close(); release_db_connection(conn)


@portal_bp.route('/sso/sites', methods=['GET'])
def sso_sites():
    """Mint a short-lived shared-secret SSO token and return the builder URL that
    logs this candidate straight into sites.winthehouse.gop."""
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    if not SSO_SHARED_SECRET:
        return jsonify({'ok': False, 'error': 'SSO not configured.'}), 503
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT COALESCE(NULLIF(email,''), email1), first_name, last_name
                       FROM candidates WHERE candidate_id=%s""", (cid,))
        row = cur.fetchone()
    finally:
        cur.close(); release_db_connection(conn)
    email = (row[0] or '').strip().lower() if row else ''
    if not email:
        return jsonify({'ok': False, 'error': 'We need an email on your profile first.'}), 400
    from itsdangerous import URLSafeTimedSerializer
    tok = URLSafeTimedSerializer(SSO_SHARED_SECRET).dumps(
        {'cid': cid, 'email': email, 'first_name': row[1] or '', 'last_name': row[2] or ''},
        salt=SSO_SALT)
    return jsonify({'ok': True, 'url': f"{SITES_SSO_BASE}/sso?token={tok}"})


@portal_bp.route('/sso/yardsigns', methods=['GET'])
def sso_yardsigns():
    """Mint a short-lived token that walks this candidate past the yard-sign
    finder's site password. Nothing but a signed token opens that door."""
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    if not SSO_SHARED_SECRET:
        return jsonify({'ok': False, 'error': 'SSO not configured.'}), 503
    from itsdangerous import URLSafeTimedSerializer
    tok = URLSafeTimedSerializer(SSO_SHARED_SECRET).dumps(
        {'cid': cid}, salt=YARDSIGNS_SSO_SALT)
    return jsonify({'ok': True, 'url': f"{YARDSIGNS_SSO_BASE}/api/sso?token={tok}"})


@portal_bp.route('/consult/slots', methods=['GET'])
def consult_slots():
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    if not (gcal and gcal.is_configured()):
        return jsonify({'ok': False, 'error': 'Scheduling is not available yet.'}), 503
    try:
        duration = int(request.args.get('duration', 30))
    except (TypeError, ValueError):
        duration = 30
    if duration not in gcal.ALLOWED_DURATIONS:
        duration = 30
    conn = get_db_connection(); cur = conn.cursor()
    try:
        extra = _pending_busy(cur)
    finally:
        cur.close(); release_db_connection(conn)
    try:
        slots = gcal.available_slots(duration, extra_busy=extra)
    except Exception as e:
        logger.warning("consult slots error: %s", e)
        return jsonify({'ok': False, 'error': 'Could not load availability right now.'}), 502
    return jsonify({'ok': True, 'duration': duration, 'slots': slots})


@portal_bp.route('/consult/request', methods=['POST'])
def consult_request():
    cid = _cid_from_session()
    if not cid:
        return jsonify({'ok': False, 'error': 'Not signed in.'}), 401
    if not (gcal and gcal.is_configured()):
        return jsonify({'ok': False, 'error': 'Scheduling is not available yet.'}), 503
    data = request.get_json(silent=True) or {}
    try:
        duration = int(data.get('duration') or 30)
    except (TypeError, ValueError):
        duration = 30
    if duration not in gcal.ALLOWED_DURATIONS:
        return jsonify({'ok': False, 'error': 'Pick a 15- or 30-minute consult.'}), 400
    start = (data.get('start') or '').strip()
    topic = (data.get('topic') or '').strip()[:1000]
    if not start:
        return jsonify({'ok': False, 'error': 'Pick a time.'}), 400

    conn = get_db_connection(); cur = conn.cursor()
    try:
        p = _prefill(cur, cid) or {}
        name = f"{p.get('first_name','')} {p.get('last_name','')}".strip() or 'Candidate'
        email = p.get('email', '')
        if not email:
            return jsonify({'ok': False, 'error': 'Add an email to your profile first so we can send the invite.'}), 400
        extra = _pending_busy(cur)
        try:
            if not gcal.slot_is_open(start, duration, extra_busy=extra):
                return jsonify({'ok': False, 'error': 'That time was just taken — please pick another.'}), 409
        except Exception:
            return jsonify({'ok': False, 'error': 'Could not verify availability.'}), 502
        cur.execute("SELECT 1 FROM consult_requests WHERE candidate_id=%s AND status='pending'", (cid,))
        if cur.fetchone():
            return jsonify({'ok': False, 'error': "You already have a consult request pending — we'll confirm it shortly."}), 409
        cur.execute("""INSERT INTO consult_requests
            (candidate_id, candidate_name, email, duration_min, requested_start, topic, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,'pending',NOW()) RETURNING id""",
            (cid, name, email, duration, start, topic))
        rid = cur.fetchone()[0]
        conn.commit()
    finally:
        cur.close(); release_db_connection(conn)

    if log_activity:
        log_activity('consult_requested', f'Requested a {duration}-min consult', cid)
    when = _fmt_when(start)
    atok = make_token('consult_approve', rid)
    approve_link = f"{APP_URL}/portal/api/consult/approve?token={atok}"
    _signal_notify(
        f"\U0001F4C5 NEW consult request #{rid}\n{name} — {duration} min\n{when}\n"
        + (f"Topic: {topic}\n" if topic else "")
        + f"Email: {email}\n\nApprove/decline: {approve_link}")
    try:
        send_email(CONSULT_ADMIN_EMAIL, f"Consult request — {name} ({when})",
                   f"<p><b>{name}</b> requested a {duration}-minute consult.</p>"
                   f"<p><b>When:</b> {when}<br><b>Email:</b> {email}</p>"
                   + (f"<p><b>Topic:</b> {topic}</p>" if topic else "")
                   + f'<p><a href="{approve_link}">Approve or decline &rarr;</a></p>',
                   f"{name} requested a {duration}-min consult at {when}. Approve/decline: {approve_link}")
    except Exception:
        logger.exception("consult admin email failed")
    return jsonify({'ok': True,
                    'message': "Request sent! Chris will confirm, and you'll get a calendar invite with a Google Meet link."})


@portal_bp.route('/consult/approve', methods=['GET'])
def consult_approve_page():
    token = request.args.get('token', '')
    res = read_token(token, 14 * 24 * 3600)
    if not res or res.get('type') != 'consult_approve':
        return _APPROVE_PAGE.format(body="<p>This approval link is invalid or expired.</p>"), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT candidate_name, email, duration_min, requested_start, topic, status
                       FROM consult_requests WHERE id=%s""", (res['id'],))
        row = cur.fetchone()
    finally:
        cur.close(); release_db_connection(conn)
    if not row:
        return _APPROVE_PAGE.format(body="<p>Request not found.</p>"), 404
    name, email, dur, start, topic, status = row
    when = _fmt_when(start.isoformat() if start else '')
    if status != 'pending':
        return _APPROVE_PAGE.format(body=f"<p>Already <b>{status}</b> — {name}, {when}.</p>")
    body = (f"<h2 style='margin-top:0'>Consult request</h2>"
            f"<p><b>{name}</b> &middot; {dur} min<br>{when}<br>{email}</p>"
            + (f"<p><b>Topic:</b> {topic}</p>" if topic else "")
            + f'<form method=post><input type=hidden name=token value="{token}">'
              f'<button class=btn name=action value=approve type=submit>Approve &amp; send invite</button> '
              f'<button class=btn style="background:#6b7280" name=action value=decline type=submit>Decline</button></form>')
    return _APPROVE_PAGE.format(body=body)


@portal_bp.route('/consult/approve', methods=['POST'])
def consult_approve_do():
    token = request.form.get('token', '')
    action = request.form.get('action', '')
    res = read_token(token, 14 * 24 * 3600)
    if not res or res.get('type') != 'consult_approve':
        return _APPROVE_PAGE.format(body="<p>This approval link is invalid or expired.</p>"), 400
    rid = res['id']
    ev = None
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT candidate_name, email, duration_min, requested_start, topic, status
                       FROM consult_requests WHERE id=%s FOR UPDATE""", (rid,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return _APPROVE_PAGE.format(body="<p>Request not found.</p>"), 404
        name, email, dur, start, topic, status = row
        when = _fmt_when(start.isoformat() if start else '')
        if status != 'pending':
            conn.rollback()
            return _APPROVE_PAGE.format(body=f"<p>Already <b>{status}</b> — {name}.</p>")

        if action == 'decline':
            cur.execute("UPDATE consult_requests SET status='declined', decided_at=NOW() WHERE id=%s", (rid,))
            conn.commit()
            try:
                send_email(email, "About your consult request",
                           f"<p>Hi {name.split(' ')[0] or 'there'},</p><p>Thanks for reaching out. That time "
                           f"didn't end up working — please pick another opening from your candidate profile and "
                           f"we'll get it booked.</p><p>— Chris Maidment</p>",
                           "Please pick another opening from your candidate profile.")
            except Exception:
                logger.exception("consult decline email failed")
            return _APPROVE_PAGE.format(body=f"<p>Declined — {name} notified.</p>")

        # approve -> create the Google Calendar event with a Meet link + invite
        try:
            ev = gcal.create_consult_event(
                start.isoformat(), int(dur), name, email,
                summary=f"CTEHR consult — {name}",
                description=(f"Consult with {name}." + (f"\nTopic: {topic}" if topic else "")))
        except Exception as e:
            conn.rollback()
            logger.exception("consult event create failed")
            return _APPROVE_PAGE.format(
                body=f"<p style='color:#b91c1c'>Could not create the calendar event: {e}</p>"
                     f"<p>The request is still pending — you can retry this link.</p>")
        cur.execute("""UPDATE consult_requests SET status='approved', decided_at=NOW(),
                       meet_link=%s, event_id=%s, html_link=%s WHERE id=%s""",
                    (ev['meet_link'], ev['event_id'], ev['html_link'], rid))
        conn.commit()
    finally:
        cur.close(); release_db_connection(conn)

    if log_activity:
        log_activity('consult_approved', f'Approved consult for {name} ({when})', None)
    meet_link = ev.get('meet_link') if ev else ''
    # Google sends the actual calendar invite (renders in Outlook/Gmail/Apple). This is
    # just a friendly branded confirmation with the same Meet link.
    try:
        send_email(email, f"Confirmed: your consult with Chris — {when}",
                   f"<p>Hi {name.split(' ')[0] or 'there'},</p>"
                   f"<p>Your {dur}-minute consult with Chris is confirmed for <b>{when}</b>. "
                   f"A Google Calendar invite is on its way — accept it to add it to your calendar.</p>"
                   + (f'<p style="margin:16px 0"><a href="{meet_link}" style="background:#b91c1c;color:#fff;padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:700;display:inline-block">Join Google Meet</a></p>'
                      f'<p style="font-size:13px;color:#555">Meet link: <a href="{meet_link}">{meet_link}</a></p>'
                      if meet_link else '')
                   + "<p>See you then!<br>— Chris Maidment</p>",
                   f"Your consult with Chris is confirmed for {when}. A calendar invite is on its way. "
                   + (f"Google Meet: {meet_link}" if meet_link else ""))
    except Exception:
        logger.exception("consult confirm email failed")
    return _APPROVE_PAGE.format(body=f"<p>Approved ✅ — invite + Meet link sent to {name}.</p>")


def _ics_dt(d):
    return d.astimezone(_dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _ics_escape(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _send_consult_invite(to_email, name, start_dt, end_dt, meet_link, event_id, topic="", sequence=0):
    """Email the candidate a real calendar invite (.ics) carrying the authoritative
    Meet link, so their calendar and Chris's reference the same single link. We send
    this ourselves (instead of relying on Google's service-account guest invite, whose
    Meet link can diverge) so both parties are guaranteed the same room."""
    import boto3
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    sender_email = os.environ.get('SES_SENDER_EMAIL', 'noreply@nhcandidaterecruitment.com')
    sender_name = 'Committee to Elect House Republicans'
    organizer = os.environ.get('CAL_OWNER_EMAIL', 'chris@maidmentnh.com')
    first = (name.split(' ')[0] if name else 'there')
    when = _fmt_when(start_dt.isoformat())
    uid = f"{event_id}@google.com" if event_id else f"consult-{_ics_dt(start_dt)}@electhouserepublicans.com"
    desc = f"Join Google Meet: {meet_link}" + (f"\n\nTopic: {topic}" if topic else "")

    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//CTEHR//Consult//EN", "VERSION:2.0",
        "CALSCALE:GREGORIAN", "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{_ics_dt(_dt.datetime.now(_dt.timezone.utc))}",
        f"DTSTART:{_ics_dt(start_dt)}",
        f"DTEND:{_ics_dt(end_dt)}",
        "SUMMARY:Consult with Chris Maidment (CTEHR)",
        f"DESCRIPTION:{_ics_escape(desc)}",
        f"LOCATION:{_ics_escape(meet_link)}",
        f"ORGANIZER;CN=Chris Maidment:mailto:{organizer}",
        f"ATTENDEE;CN={_ics_escape(name)};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{to_email}",
        "STATUS:CONFIRMED",
        "BEGIN:VALARM", "TRIGGER:-PT10M", "ACTION:DISPLAY", "DESCRIPTION:Consult with Chris", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR"]) + "\r\n"

    html = (f"<p>Hi {first},</p>"
            f"<p>Your consult with Chris is confirmed for <b>{when}</b>.</p>"
            f'<p style="margin:18px 0"><a href="{meet_link}" style="background:#b91c1c;color:#fff;padding:13px 26px;border-radius:6px;text-decoration:none;font-weight:700;display:inline-block;font-size:16px">Join Google Meet</a></p>'
            f'<p style="font-size:13px;color:#555;line-height:1.5">Meet link: <a href="{meet_link}">{meet_link}</a><br>'
            f"This invite has been added to your calendar. <b>Please join using this exact link</b> so we’re in the same room — ignore any other Meet link you may have received.</p>"
            "<p>— Chris Maidment</p>")
    text = (f"Hi {first},\n\nYour consult with Chris is confirmed for {when}.\n"
            f"Join Google Meet (use THIS exact link): {meet_link}\n\n— Chris Maidment")

    root = MIMEMultipart('mixed')
    root['Subject'] = f"Confirmed: your consult with Chris — {when}"
    root['From'] = f'"{sender_name}" <{sender_email}>'
    root['To'] = to_email
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(text, 'plain', 'utf-8'))
    alt.attach(MIMEText(html, 'html', 'utf-8'))
    cal = MIMEText(ics, 'calendar', 'utf-8')
    cal.set_param('method', 'REQUEST')
    alt.attach(cal)
    root.attach(alt)
    att = MIMEBase('application', 'ics')
    att.set_payload(ics)
    encoders.encode_base64(att)
    att.add_header('Content-Disposition', 'attachment; filename="consult.ics"')
    root.attach(att)

    ses = boto3.client('ses', region_name=os.environ.get('AWS_REGION', 'us-east-1'),
                       aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                       aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'))
    ses.send_raw_email(Source=root['From'], Destinations=[to_email],
                       RawMessage={'Data': root.as_string()})
