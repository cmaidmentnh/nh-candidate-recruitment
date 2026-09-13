"""
Candidate intake — public API behind electhouserepublicans.com/candidates.

Candidates verify their email with a 6-digit code, then submit contact info
and photos. Verified-email matches are applied straight onto the existing
candidates record (single source of truth); everything else lands in a
pending queue at /intake/admin for review. Prefill pulls from candidates,
candidate_election_status, filings, and the website-builder ws_* tables.

The requests arrive proxied through the ctehr-website Node server, which
handles Cloudflare Turnstile before forwarding.
"""

import json
import re
import secrets
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash
from flask_login import current_user
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

intake_bp = Blueprint('intake', __name__, url_prefix='/intake')

# Injected by init_candidate_intake()
get_db_connection = None
release_db_connection = None
upload_file_to_storage = None
send_email = None
log_activity = None

CODE_TTL_MINUTES = 15
TOKEN_TTL_MINUTES = 120
MAX_CODES_PER_EMAIL_PER_HOUR = 4
MAX_VERIFY_ATTEMPTS = 5
ALLOWED_PHOTO_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'}
MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_EXTRA_PHOTOS = 5

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def init_candidate_intake(db_get, db_release, storage_upload, email_send, activity_log):
    global get_db_connection, release_db_connection, upload_file_to_storage, send_email, log_activity
    get_db_connection = db_get
    release_db_connection = db_release
    upload_file_to_storage = storage_upload
    send_email = email_send
    log_activity = activity_log


def intake_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or getattr(current_user, 'is_candidate', False):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Public JSON API (proxied from electhouserepublicans.com)
# ---------------------------------------------------------------------------

@intake_bp.route('/api/start', methods=['POST'])
def api_start():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 255:
        return jsonify({'ok': False, 'error': 'Please enter a valid email address.'}), 400

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COUNT(*) FROM intake_verifications
            WHERE LOWER(email) = %s AND created_at > NOW() - INTERVAL '1 hour'
        """, (email,))
        if cur.fetchone()[0] >= MAX_CODES_PER_EMAIL_PER_HOUR:
            # Silently accept so the endpoint can't be used to probe or spam
            return jsonify({'ok': True})

        code = f"{secrets.randbelow(1000000):06d}"
        cur.execute("""
            INSERT INTO intake_verifications (email, code, expires_at)
            VALUES (%s, %s, NOW() + make_interval(mins => %s))
        """, (email, code, CODE_TTL_MINUTES))
        conn.commit()
    finally:
        cur.close(); release_db_connection(conn)

    subject = f"Your verification code: {code}"
    html_body = f"""
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: #d91720; padding: 18px; text-align: center;">
            <h1 style="color: white; margin: 0; font-size: 20px;">Committee to Elect House Republicans</h1>
        </div>
        <div style="background: #f8f9fa; padding: 30px; border: 1px solid #e9ecef; border-top: none;">
            <p>Use this code to verify your email on the CTEHR candidate information page:</p>
            <p style="font-size: 34px; font-weight: bold; letter-spacing: 8px; text-align: center; margin: 24px 0;">{code}</p>
            <p style="color: #666; font-size: 14px;">This code expires in {CODE_TTL_MINUTES} minutes. If you didn't request it, you can safely ignore this email.</p>
        </div>
    </body>
    """
    text_body = f"Your CTEHR verification code is {code}. It expires in {CODE_TTL_MINUTES} minutes."
    send_email(email, subject, html_body, text_body)
    return jsonify({'ok': True})


@intake_bp.route('/api/verify', methods=['POST'])
def api_verify():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    code = re.sub(r'\D', '', (data.get('code') or ''))
    if not email or len(code) != 6:
        return jsonify({'ok': False, 'error': 'Enter the 6-digit code from your email.'}), 400

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT verification_id, code, attempts FROM intake_verifications
            WHERE LOWER(email) = %s AND verified_at IS NULL AND expires_at > NOW()
            ORDER BY created_at DESC LIMIT 1
        """, (email,))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Code expired or not found. Request a new one.'}), 400
        vid, real_code, attempts = row
        if attempts >= MAX_VERIFY_ATTEMPTS:
            return jsonify({'ok': False, 'error': 'Too many attempts. Request a new code.'}), 429
        if code != real_code:
            cur.execute("UPDATE intake_verifications SET attempts = attempts + 1 WHERE verification_id = %s", (vid,))
            conn.commit()
            return jsonify({'ok': False, 'error': 'That code is not correct. Check your email and try again.'}), 400

        token = secrets.token_urlsafe(48)
        cur.execute("""
            UPDATE intake_verifications SET verified_at = NOW(), token = %s WHERE verification_id = %s
        """, (token, vid))
        conn.commit()

        profile, matched = _build_prefill(cur, email)
        towns = _towns_list(cur)
        return jsonify({'ok': True, 'token': token, 'profile': profile, 'matched': matched, 'towns': towns})
    finally:
        cur.close(); release_db_connection(conn)


@intake_bp.route('/api/submit', methods=['POST'])
def api_submit():
    token = (request.form.get('token') or '').strip()
    if not token:
        return jsonify({'ok': False, 'error': 'Missing verification token.'}), 400

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT verification_id, email FROM intake_verifications
            WHERE token = %s AND verified_at IS NOT NULL AND used_at IS NULL
              AND verified_at > NOW() - make_interval(mins => %s)
        """, (token, TOKEN_TTL_MINUTES))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Your session expired. Please start over and verify your email again.'}), 400
        vid, email = row
        email = email.lower()

        def field(name, maxlen=255):
            return (request.form.get(name) or '').strip()[:maxlen]

        sub = {
            'first_name': field('first_name', 100), 'last_name': field('last_name', 100),
            'phone1': field('phone1', 50), 'phone2': field('phone2', 50),
            'address': field('address'), 'city': field('city', 100), 'zip': field('zip', 20),
            'town': field('town', 100), 'district_code': field('district_code', 50),
            'facebook': field('facebook', 500), 'twitter_x': field('twitter_x', 500),
            'instagram': field('instagram', 500), 'website': field('website', 500),
            'notes': (request.form.get('notes') or '').strip()[:5000],
        }
        if not sub['first_name'] or not sub['last_name']:
            return jsonify({'ok': False, 'error': 'First and last name are required.'}), 400

        # Photo uploads → DigitalOcean Spaces
        headshot_url = None
        photo_urls = []
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
                return jsonify({'ok': False, 'error': f'"{f.filename}" is not a supported image type (JPG, PNG, GIF, WEBP, HEIC).'}), 400
            f.seek(0, 2)
            size = f.tell()
            f.seek(0)
            if size > MAX_PHOTO_BYTES:
                return jsonify({'ok': False, 'error': f'"{f.filename}" is over 15MB. Please upload a smaller file.'}), 400
            dest = f"candidate_intake/{vid}/{secrets.token_hex(4)}_{secure_filename(f.filename)}"
            url = upload_file_to_storage(f, dest)
            if not url:
                return jsonify({'ok': False, 'error': 'Photo upload failed. Please try again.'}), 500
            if kind == 'headshot':
                headshot_url = url
            else:
                photo_urls.append(url)

        match_id = _match_candidate_by_email(cur, email)

        cur.execute("""
            INSERT INTO intake_submissions
                (verification_id, email, first_name, last_name, phone1, phone2, address, city, zip,
                 town, district_code, facebook, twitter_x, instagram, website, notes,
                 headshot_url, photo_urls, matched_candidate_id, auto_applied, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING submission_id
        """, (vid, email, sub['first_name'], sub['last_name'], sub['phone1'], sub['phone2'],
              sub['address'], sub['city'], sub['zip'], sub['town'], sub['district_code'],
              sub['facebook'], sub['twitter_x'], sub['instagram'], sub['website'], sub['notes'],
              headshot_url, json.dumps(photo_urls), match_id,
              match_id is not None, 'applied' if match_id else 'pending'))
        submission_id = cur.fetchone()[0]

        if match_id:
            _apply_to_candidate(cur, match_id, email, sub, headshot_url, photo_urls,
                                applied_by='intake@electhouserepublicans.com')

        cur.execute("UPDATE intake_verifications SET used_at = NOW() WHERE verification_id = %s", (vid,))
        conn.commit()

        if match_id:
            log_activity('intake_applied',
                         f"Intake submission #{submission_id} from {email} auto-applied via electhouserepublicans.com/candidates",
                         match_id)
        else:
            log_activity('intake_pending',
                         f"Intake submission #{submission_id} from {email} ({sub['first_name']} {sub['last_name']}) awaiting admin review")
        return jsonify({'ok': True, 'applied': match_id is not None})
    except Exception as e:
        conn.rollback()
        logger.error(f"Intake submit error: {e}")
        return jsonify({'ok': False, 'error': 'Something went wrong saving your information. Please try again.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


# ---------------------------------------------------------------------------
# Prefill / matching helpers
# ---------------------------------------------------------------------------

def _match_candidate_by_email(cur, email):
    """Candidate this verified email belongs to: direct email-field match, or
    the website-builder account link. Returns candidate_id only when the
    match is unambiguous."""
    cur.execute("""
        SELECT DISTINCT candidate_id FROM candidates
        WHERE LOWER(email) = %s OR LOWER(email1) = %s OR LOWER(email2) = %s
    """, (email, email, email))
    ids = {r[0] for r in cur.fetchall()}
    try:
        cur.execute("""
            SELECT recruitment_candidate_id FROM ws_candidates
            WHERE LOWER(email) = %s AND recruitment_candidate_id IS NOT NULL
        """, (email,))
        ids |= {r[0] for r in cur.fetchall()}
    except Exception:
        pass  # ws_* tables belong to the website builder; tolerate absence
    return ids.pop() if len(ids) == 1 else None


def _build_prefill(cur, email):
    """Best-known profile for this email across the tracker, filings, and the
    website builder. Tracker data wins; ws_* fills the gaps."""
    profile = {}
    matched = False
    candidate_id = _match_candidate_by_email(cur, email)

    if candidate_id:
        matched = True
        cur.execute("""
            SELECT first_name, last_name, phone1, phone2, address, city, zip,
                   twitter_x, facebook, instagram, photo_url
            FROM candidates WHERE candidate_id = %s
        """, (candidate_id,))
        r = cur.fetchone()
        keys = ['first_name', 'last_name', 'phone1', 'phone2', 'address', 'city', 'zip',
                'twitter_x', 'facebook', 'instagram', 'photo_url']
        profile = {k: (v or '') for k, v in zip(keys, r)}

        cur.execute("""
            SELECT district_code FROM candidate_election_status
            WHERE candidate_id = %s AND election_year = 2026 LIMIT 1
        """, (candidate_id,))
        row = cur.fetchone()
        if row:
            profile['district_code'] = row[0]
        cur.execute("""
            SELECT district_code, town FROM filings
            WHERE candidate_id = %s AND election_year = 2026 LIMIT 1
        """, (candidate_id,))
        row = cur.fetchone()
        if row:
            profile.setdefault('district_code', row[0])
            profile['town'] = row[1] or ''

    # Website-builder data (same database) fills whatever is still blank
    try:
        cur.execute("""
            SELECT s.contact_phone, s.mailing_address, s.website_url,
                   s.facebook_url, s.twitter_url, s.instagram_url, c.first_name, c.last_name
            FROM ws_candidates c
            LEFT JOIN ws_submissions s ON s.candidate_id = c.id
            WHERE LOWER(c.email) = %s
            ORDER BY s.updated_at DESC NULLS LAST LIMIT 1
        """, (email,))
        r = cur.fetchone()
        if r:
            ws = dict(zip(['phone1', 'address', 'website', 'facebook', 'twitter_x',
                           'instagram', 'first_name', 'last_name'], r))
            for k, v in ws.items():
                if v and not profile.get(k):
                    profile[k] = v
    except Exception:
        pass

    profile.setdefault('email', email)
    return profile, matched


def _towns_list(cur):
    cur.execute("""
        SELECT DISTINCT ON (display) display, full_district_code FROM (
            SELECT CASE WHEN ward IS NOT NULL AND ward != 0
                        THEN town || ' Ward ' || ward ELSE town END AS display,
                   full_district_code
            FROM districts
        ) t ORDER BY display, full_district_code
    """)
    return [{'town': r[0], 'district_code': r[1]} for r in cur.fetchall()]


def _apply_to_candidate(cur, candidate_id, email, sub, headshot_url, photo_urls, applied_by):
    """Merge a submission onto the canonical candidates row. Only overwrites
    with non-empty submitted values; never touches login email, password,
    or the legislator-metadata `other` column."""
    updatable = ['first_name', 'last_name', 'phone1', 'phone2', 'address', 'city', 'zip',
                 'facebook', 'twitter_x', 'instagram']
    sets, params = [], []
    for col in updatable:
        if sub.get(col):
            sets.append(f"{col} = %s")
            params.append(sub[col])
    if headshot_url:
        sets.append("photo_url = %s")
        params.append(headshot_url)
    if sets:
        params.append(candidate_id)
        cur.execute(f"UPDATE candidates SET {', '.join(sets)} WHERE candidate_id = %s", params)

    # Make sure the verified email is on the record somewhere
    cur.execute("SELECT email, email1, email2 FROM candidates WHERE candidate_id = %s", (candidate_id,))
    e0, e1, e2 = [(x or '').lower() for x in cur.fetchone()]
    if email not in (e0, e1, e2):
        if not e1:
            cur.execute("UPDATE candidates SET email1 = %s WHERE candidate_id = %s", (email, candidate_id))
        elif not e2:
            cur.execute("UPDATE candidates SET email2 = %s WHERE candidate_id = %s", (email, candidate_id))

    note_bits = [f"Submitted info via electhouserepublicans.com/candidates ({email})."]
    if sub.get('town'):
        note_bits.append(f"Town: {sub['town']}.")
    if sub.get('website'):
        note_bits.append(f"Website: {sub['website']}.")
    if sub.get('notes'):
        note_bits.append(f"Notes: {sub['notes']}")
    if photo_urls:
        note_bits.append("Additional photos: " + " ".join(photo_urls))
    cur.execute("""
        INSERT INTO comments (candidate_id, comment_text, added_by)
        VALUES (%s, %s, %s)
    """, (candidate_id, " ".join(note_bits), applied_by))


# ---------------------------------------------------------------------------
# Admin review queue
# ---------------------------------------------------------------------------

@intake_bp.route('/admin')
@intake_admin_required
def admin_queue():
    show = request.args.get('show', 'pending')
    conn = get_db_connection(); cur = conn.cursor()
    try:
        where = "s.status = 'pending'" if show == 'pending' else "s.status != 'pending'"
        cur.execute(f"""
            SELECT s.submission_id, s.email, s.first_name, s.last_name, s.phone1, s.phone2,
                   s.address, s.city, s.zip, s.town, s.district_code, s.facebook, s.twitter_x,
                   s.instagram, s.website, s.notes, s.headshot_url, s.photo_urls,
                   s.matched_candidate_id, s.auto_applied, s.status, s.reviewed_by,
                   s.reviewed_at, s.created_at,
                   c.first_name, c.last_name
            FROM intake_submissions s
            LEFT JOIN candidates c ON c.candidate_id = s.matched_candidate_id
            WHERE {where}
            ORDER BY s.created_at DESC LIMIT 200
        """)
        cols = ['submission_id', 'email', 'first_name', 'last_name', 'phone1', 'phone2',
                'address', 'city', 'zip', 'town', 'district_code', 'facebook', 'twitter_x',
                'instagram', 'website', 'notes', 'headshot_url', 'photo_urls',
                'matched_candidate_id', 'auto_applied', 'status', 'reviewed_by',
                'reviewed_at', 'created_at', 'matched_first', 'matched_last']
        subs = [dict(zip(cols, r)) for r in cur.fetchall()]

        # Name-based match suggestions for the pending ones
        for s in subs:
            s['suggestions'] = []
            if s['status'] != 'pending':
                continue
            cur.execute("""
                SELECT c.candidate_id, c.first_name, c.last_name, c.email, c.city,
                       ces.district_code
                FROM candidates c
                LEFT JOIN candidate_election_status ces
                       ON ces.candidate_id = c.candidate_id AND ces.election_year = 2026
                WHERE LOWER(c.last_name) = LOWER(%s)
                  AND LOWER(SPLIT_PART(c.first_name, ' ', 1)) LIKE LOWER(%s) || '%%'
                ORDER BY c.candidate_id LIMIT 5
            """, (s['last_name'] or '', (s['first_name'] or '')[:3]))
            s['suggestions'] = [dict(zip(
                ['candidate_id', 'first_name', 'last_name', 'email', 'city', 'district_code'], r))
                for r in cur.fetchall()]

        cur.execute("SELECT COUNT(*) FROM intake_submissions WHERE status = 'pending'")
        pending_count = cur.fetchone()[0]
    finally:
        cur.close(); release_db_connection(conn)
    return render_template('intake_admin.html', submissions=subs, show=show, pending_count=pending_count)


def _load_submission(cur, submission_id):
    cur.execute("""
        SELECT submission_id, email, first_name, last_name, phone1, phone2, address, city, zip,
               town, district_code, facebook, twitter_x, instagram, website, notes,
               headshot_url, photo_urls, status
        FROM intake_submissions WHERE submission_id = %s
    """, (submission_id,))
    r = cur.fetchone()
    if not r:
        return None
    cols = ['submission_id', 'email', 'first_name', 'last_name', 'phone1', 'phone2', 'address',
            'city', 'zip', 'town', 'district_code', 'facebook', 'twitter_x', 'instagram',
            'website', 'notes', 'headshot_url', 'photo_urls', 'status']
    return dict(zip(cols, r))


@intake_bp.route('/admin/<int:submission_id>/apply', methods=['POST'])
@intake_admin_required
def admin_apply(submission_id):
    candidate_id = request.form.get('candidate_id', type=int)
    if not candidate_id:
        flash("Pick a candidate to apply this submission to.", "warning")
        return redirect(url_for('intake.admin_queue'))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        sub = _load_submission(cur, submission_id)
        if not sub or sub['status'] != 'pending':
            flash("Submission not found or already handled.", "warning")
            return redirect(url_for('intake.admin_queue'))
        photo_urls = sub['photo_urls'] if isinstance(sub['photo_urls'], list) else json.loads(sub['photo_urls'] or '[]')
        _apply_to_candidate(cur, candidate_id, sub['email'].lower(), sub,
                            sub['headshot_url'], photo_urls, applied_by=current_user.email)
        cur.execute("""
            UPDATE intake_submissions
            SET status = 'applied', matched_candidate_id = %s, reviewed_by = %s, reviewed_at = NOW()
            WHERE submission_id = %s
        """, (candidate_id, current_user.email, submission_id))
        conn.commit()
        log_activity('intake_applied', f"Intake submission #{submission_id} applied to candidate by {current_user.email}", candidate_id)
        flash("Submission applied to candidate record.", "success")
    except Exception as e:
        conn.rollback()
        logger.error(f"Intake admin apply error: {e}")
        flash("Error applying submission.", "danger")
    finally:
        cur.close(); release_db_connection(conn)
    return redirect(url_for('intake.admin_queue'))


@intake_bp.route('/admin/<int:submission_id>/create', methods=['POST'])
@intake_admin_required
def admin_create(submission_id):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        sub = _load_submission(cur, submission_id)
        if not sub or sub['status'] != 'pending':
            flash("Submission not found or already handled.", "warning")
            return redirect(url_for('intake.admin_queue'))
        cur.execute("""
            INSERT INTO candidates (first_name, last_name, email, party, phone1, phone2,
                                    address, city, zip, facebook, twitter_x, instagram,
                                    photo_url, created_by)
            VALUES (%s,%s,%s,'R',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING candidate_id
        """, (sub['first_name'], sub['last_name'], sub['email'].lower(), sub['phone1'], sub['phone2'],
              sub['address'], sub['city'], sub['zip'], sub['facebook'], sub['twitter_x'],
              sub['instagram'], sub['headshot_url'], current_user.email))
        candidate_id = cur.fetchone()[0]
        if sub['district_code']:
            cur.execute("""
                INSERT INTO candidate_election_status (candidate_id, election_year, district_code, status, is_running, added_by)
                VALUES (%s, 2026, %s, 'Confirmed', TRUE, %s)
            """, (candidate_id, sub['district_code'], current_user.email))
        photo_urls = sub['photo_urls'] if isinstance(sub['photo_urls'], list) else json.loads(sub['photo_urls'] or '[]')
        note_bits = [f"Created from electhouserepublicans.com/candidates intake ({sub['email']})."]
        if sub['notes']:
            note_bits.append(f"Notes: {sub['notes']}")
        if sub['website']:
            note_bits.append(f"Website: {sub['website']}.")
        if photo_urls:
            note_bits.append("Additional photos: " + " ".join(photo_urls))
        cur.execute("INSERT INTO comments (candidate_id, comment_text, added_by) VALUES (%s, %s, %s)",
                    (candidate_id, " ".join(note_bits), current_user.email))
        cur.execute("""
            UPDATE intake_submissions
            SET status = 'applied', matched_candidate_id = %s, reviewed_by = %s, reviewed_at = NOW()
            WHERE submission_id = %s
        """, (candidate_id, current_user.email, submission_id))
        conn.commit()
        log_activity('intake_created', f"New candidate created from intake submission #{submission_id} by {current_user.email}", candidate_id)
        flash(f"New candidate created: {sub['first_name']} {sub['last_name']}.", "success")
    except Exception as e:
        conn.rollback()
        logger.error(f"Intake admin create error: {e}")
        flash("Error creating candidate from submission.", "danger")
    finally:
        cur.close(); release_db_connection(conn)
    return redirect(url_for('intake.admin_queue'))


@intake_bp.route('/admin/<int:submission_id>/dismiss', methods=['POST'])
@intake_admin_required
def admin_dismiss(submission_id):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE intake_submissions SET status = 'dismissed', reviewed_by = %s, reviewed_at = NOW()
            WHERE submission_id = %s AND status = 'pending'
        """, (current_user.email, submission_id))
        conn.commit()
        flash("Submission dismissed.", "info")
    finally:
        cur.close(); release_db_connection(conn)
    return redirect(url_for('intake.admin_queue'))
