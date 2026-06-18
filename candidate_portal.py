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
               twitter_x, facebook, instagram, photo_url, email, username,
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


def _send_access_link(cid, fn, email):
    token = make_token('portal_access', cid)
    link = f"{PORTAL_BASE}/candidates.html?token={token}"
    html = f"""<div style="font-family:Arial,sans-serif;font-size:15px;color:#222;line-height:1.6">
        <p>Hi {fn or 'there'},</p>
        <p>Your candidate profile with the Committee to Elect House Republicans is ready. Click below to
        set your username and password and update your information.</p>
        <p style="margin:24px 0"><a href="{link}" style="background:#b91c1c;color:#fff;padding:14px 28px;
        border-radius:6px;text-decoration:none;font-weight:700">Access My Profile</a></p>
        <p style="font-size:13px;color:#444">Or paste this link into your browser:<br>
        <a href="{link}" style="color:#b91c1c;word-break:break-all">{link}</a></p>
        <p style="color:#666;font-size:13px">This link expires in 7 days.</p></div>"""
    send_email(email, "Access your candidate profile", html,
               f"Access your candidate profile: {link}\nThis link expires in 7 days.")


def _match_by_name_town(cur, name, town):
    """Best-guess match of a registrant (name + town) to a candidate record, or None."""
    toks = [p for p in re.split(r'\s+', (name or '').strip()) if p]
    if len(toks) < 2:
        return None
    first, last = toks[0], toks[-1]
    last_clean = re.sub(r'\s+(jr|sr|ii|iii|iv|v)\.?$', '', last, flags=re.I)
    cur.execute("""SELECT candidate_id FROM candidates
                   WHERE LOWER(REGEXP_REPLACE(last_name,'\\s+(jr|sr|ii|iii|iv|v)\\.?$','','i')) = LOWER(%s)
                     AND LOWER(SPLIT_PART(TRIM(first_name),' ',1)) = LOWER(%s)""", (last_clean, first))
    ids = [r[0] for r in cur.fetchall()]
    if len(ids) == 1:
        return ids[0]
    if not ids:
        return None
    if town:  # disambiguate multiple same-name people by the town's district
        cur.execute("SELECT DISTINCT full_district_code FROM districts WHERE UPPER(town)=UPPER(%s)", (town,))
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
        cur.execute("SELECT first_name, username, password_hash FROM candidates WHERE candidate_id=%s", (cid,))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Account not found.'}), 404
        first_name, username, pwhash = row
        if username and pwhash:
            session = make_token('portal_session', cid)
            return jsonify({'ok': True, 'needs_setup': False, 'session': session, 'first_name': first_name or ''})
        return jsonify({'ok': True, 'needs_setup': True, 'setup_token': tok, 'first_name': first_name or ''})
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
