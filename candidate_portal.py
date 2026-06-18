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
               twitter_x, facebook, instagram, photo_url, email, username
        FROM candidates WHERE candidate_id = %s
    """, (candidate_id,))
    r = cur.fetchone()
    if not r:
        return None
    keys = ['first_name', 'last_name', 'phone1', 'phone2', 'address', 'city', 'zip',
            'twitter_x', 'facebook', 'instagram', 'photo_url', 'email', 'username']
    p = {k: (v or '') for k, v in zip(keys, r)}
    cur.execute("""SELECT district_code FROM candidate_election_status
                   WHERE candidate_id=%s AND election_year=2026 LIMIT 1""", (candidate_id,))
    row = cur.fetchone()
    if row:
        p['district_code'] = row[0]
    cur.execute("""SELECT district_code, town FROM filings
                   WHERE candidate_id=%s AND election_year=2026 LIMIT 1""", (candidate_id,))
    row = cur.fetchone()
    if row:
        p.setdefault('district_code', row[0]); p['town'] = row[1] or ''
    return p


def _towns_list(cur):
    cur.execute("""
        SELECT DISTINCT ON (display) display, full_district_code FROM (
            SELECT CASE WHEN ward IS NOT NULL AND ward != 0
                        THEN town || ' Ward ' || ward ELSE town END AS display, full_district_code
            FROM districts) t ORDER BY display, full_district_code
    """)
    return [{'town': r[0], 'district_code': r[1]} for r in cur.fetchall()]


@portal_bp.route('/register-start', methods=['POST'])
def register_start():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()[:120]
    email = (data.get('email') or '').strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({'ok': False, 'error': 'Please enter a valid email address.'}), 400

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT candidate_id, first_name, last_name FROM candidates
                       WHERE LOWER(email) = %s ORDER BY candidate_id LIMIT 1""", (email,))
        row = cur.fetchone()
        if not row:
            who = name or email
            _signal_notify(f"Candidate {who} tried to register on the candidate portal "
                           f"but we don't have an email on file for them ({email}).")
            return jsonify({'ok': True, 'found': False, 'message': CONTACT_MSG})

        cid, fn, ln = row
        token = make_token('portal_access', cid)
        link = f"{PORTAL_BASE}/candidates.html?token={token}"
        subject = "Access your candidate profile"
        html = f"""<div style="font-family:Arial,sans-serif;font-size:15px;color:#222;line-height:1.6">
            <p>Hi {fn or 'there'},</p>
            <p>Click below to access your candidate profile with the Committee to Elect House Republicans.
            You'll set a username and password the first time.</p>
            <p style="margin:24px 0"><a href="{link}" style="background:#b91c1c;color:#fff;padding:14px 28px;
            border-radius:6px;text-decoration:none;font-weight:700">Access My Profile</a></p>
            <p style="color:#666;font-size:13px">This link expires in 7 days. If you didn't request it, you can ignore this email.</p>
            </div>"""
        text = f"Access your candidate profile: {link}\nThis link expires in 7 days."
        send_email(email, subject, html, text)
        return jsonify({'ok': True, 'found': True,
                        'message': f"We've emailed a secure access link to {email}."})
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
        return jsonify({'ok': False, 'error': 'Enter your username and password.'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT candidate_id, password_hash FROM candidates WHERE LOWER(username)=LOWER(%s)", (username,))
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
