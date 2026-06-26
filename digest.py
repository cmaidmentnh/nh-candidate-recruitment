"""
Weekly Candidate Digest
=======================
Admin composes a weekly digest of events/trainings and sends it to all filed
Republican House candidates. Candidates can submit events for the next digest
(public form, admin-reviewed) and can unsubscribe from the digest only while
staying on the main distribution list.

Private admin routes live under /private/digest (feature slug 'digest').
Public routes (/digest/submit, /digest/unsubscribe) require no login.
"""
import os, threading, html as _html
from datetime import datetime, date
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, current_app)
from flask_login import current_user
from itsdangerous import URLSafeSerializer, BadSignature

from private_features import require_feature_access  # reuses access control + super-admin

digest_bp = Blueprint('digest', __name__)

# wired by init_digest()
_get_db = None
_release_db = None
SUPER_ADMIN_EMAIL = None
SECRET = None

# Candidate-facing base URL (for links inside the email + public forms)
DIGEST_BASE_URL = os.environ.get('DIGEST_BASE_URL', 'https://electhouserepublicans.com')
DIGEST_FROM = os.environ.get('DIGEST_FROM',
                             'Committee to Elect House Republicans <digest@electhouserepublicans.com>')
DIGEST_REPLYTO = os.environ.get('DIGEST_REPLYTO', 'chris@maidmentnh.com')

# Resource links highlighted in every digest
LINK_WEBSITE = 'https://sites.winthehouse.gop'
LINK_SURVEYS = 'https://surveys.winthehouse.gop'
LINK_CANDIDATES = 'https://candidates.electhouserepublicans.com'
LINK_RESULTS = 'https://elections.nhhouse.gop'

CATEGORIES = ['Event', 'Training', 'Deadline', 'Resource', 'Other']

COMMITTEE = [
    ('Jason Osborne', 'Chairman', '(603) 391-2138'),
    ('Jim Kofalt', 'Treasurer', '(603) 264-2647'),
    ('Ross Berry', 'Vice Chair', '(603) 803-3448'),
    ('Chris Maidment', 'Executive Director', '(540) 598-1130'),
]


def init_digest(get_db, release_db, super_admin_email, secret):
    global _get_db, _release_db, SUPER_ADMIN_EMAIL, SECRET
    _get_db = get_db
    _release_db = release_db
    SUPER_ADMIN_EMAIL = super_admin_email
    SECRET = secret


def _serializer():
    return URLSafeSerializer(SECRET or 'digest-fallback', salt='digest-unsub')


def _unsub_token(email):
    return _serializer().dumps(email.lower())


def _esc(s):
    return _html.escape(s or '')


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #
def _recipients(cur):
    """All filed 2026 R State Rep candidates with a usable email, minus digest opt-outs.
    Returns list of (name, email)."""
    cur.execute("""
        WITH cand AS (
            SELECT DISTINCT ON (lower(COALESCE(NULLIF(c.email,''),NULLIF(c.email1,''),NULLIF(c.email2,''))))
                   f.first_name||' '||f.last_name AS name,
                   COALESCE(NULLIF(c.email,''),NULLIF(c.email1,''),NULLIF(c.email2,'')) AS email,
                   c.dead_email
            FROM filings f
            JOIN candidates c ON c.candidate_id = f.candidate_id
            WHERE f.election_year = 2026 AND f.party = 'R'
              AND f.office = 'State Representative'
              AND COALESCE(NULLIF(c.email,''),NULLIF(c.email1,''),NULLIF(c.email2,'')) IS NOT NULL
        )
        SELECT name, email FROM cand
        WHERE position('@' in email) > 1
          AND (dead_email IS NULL OR lower(dead_email) NOT LIKE '%'||lower(email)||'%')
          AND lower(email) NOT IN (SELECT lower(email) FROM digest_unsubscribes)
        ORDER BY name
    """)
    seen, out = set(), []
    for name, email in cur.fetchall():
        k = email.lower().strip()
        if k in seen:
            continue
        seen.add(k)
        out.append((name, email.strip()))
    return out


# --------------------------------------------------------------------------- #
# Email rendering
# --------------------------------------------------------------------------- #
def _fmt_date(d):
    if not d:
        return ''
    try:
        return d.strftime('%A, %B %-d')
    except Exception:
        return str(d)


def render_digest_html(intro, events, unsub_url):
    NAVY, RED, INK, MUTE = '#1e3557', '#c6312d', '#1a1a1a', '#6b7280'
    cat_color = {'Event': NAVY, 'Training': '#1e7a3c', 'Deadline': RED,
                 'Resource': '#7c3aed', 'Other': MUTE}

    def btn(href, label, color):
        return (f'<a href="{href}" style="display:inline-block;padding:11px 18px;margin:4px;'
                f'background:{color};color:#fff;text-decoration:none;border-radius:6px;'
                f'font-weight:600;font-size:14px">{label}</a>')

    ev_html = ''
    if events:
        for e in events:
            badge = (f'<span style="display:inline-block;background:{cat_color.get(e["category"],NAVY)};'
                     f'color:#fff;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;'
                     f'padding:2px 8px;border-radius:10px">{_esc(e["category"])}</span>')
            title = _esc(e['title'])
            if e.get('url'):
                title = f'<a href="{_esc(e["url"])}" style="color:{NAVY};text-decoration:none">{title} &rsaquo;</a>'
            meta = []
            if e.get('event_date'):
                meta.append(_fmt_date(e['event_date']))
            if e.get('event_time'):
                meta.append(_esc(e['event_time']))
            if e.get('location'):
                meta.append(_esc(e['location']))
            meta_html = (f'<div style="color:{MUTE};font-size:13px;margin:4px 0 0">'
                         + ' &nbsp;|&nbsp; '.join(meta) + '</div>') if meta else ''
            desc = (f'<div style="color:{INK};font-size:14px;line-height:1.5;margin:8px 0 0">'
                    f'{_esc(e["description"])}</div>') if e.get('description') else ''
            ev_html += (f'<div style="border:1px solid #e5e7eb;border-left:4px solid '
                        f'{cat_color.get(e["category"],NAVY)};border-radius:8px;padding:14px 16px;margin:0 0 12px">'
                        f'{badge}<div style="font-size:17px;font-weight:700;color:{NAVY};margin:6px 0 0">{title}</div>'
                        f'{meta_html}{desc}</div>')
    else:
        ev_html = (f'<div style="color:{MUTE};font-size:14px;font-style:italic">'
                   'No events listed this week — check back next week, or submit one below.</div>')

    intro_html = ''.join(f'<p style="margin:0 0 12px">{_esc(p)}</p>'
                         for p in (intro or '').split('\n\n') if p.strip())

    committee_html = ''
    for name, title, phone in COMMITTEE:
        committee_html += (f'<tr><td style="padding:2px 14px 2px 0;font-weight:700;color:#fff;font-size:13px">{name}</td>'
                           f'<td style="padding:2px 14px 2px 0;color:#cbd5e1;font-size:13px">{title}</td>'
                           f'<td style="padding:2px 0;color:#cbd5e1;font-size:13px">{phone}</td></tr>')

    today = date.today().strftime('%B %-d, %Y')
    submit_url = DIGEST_BASE_URL + '/digest/submit'

    return f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f4f6fa">
<div style="max-width:600px;margin:0 auto;background:#fff;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
  <div style="background:{NAVY};padding:22px 28px">
    <div style="color:#fff;font-size:20px;font-weight:800;letter-spacing:.01em">Win the House — Weekly Digest</div>
    <div style="color:#9fb3d1;font-size:13px;margin-top:2px">Committee to Elect House Republicans &nbsp;·&nbsp; {today}</div>
  </div>
  <div style="padding:24px 28px">
    <div style="color:{INK};font-size:15px;line-height:1.6">{intro_html}</div>

    <div style="font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;color:{RED};margin:8px 0 14px">
      Upcoming Events &amp; Trainings</div>
    {ev_html}

    <div style="text-align:center;margin:22px 0 6px">
      {btn(LINK_WEBSITE,'Build Your Website',NAVY)}
      {btn(LINK_SURVEYS,'Take a Survey',NAVY)}
      {btn(LINK_CANDIDATES,'Public Candidate List',NAVY)}
      {btn(LINK_RESULTS,'Previous Election Results',NAVY)}
    </div>
    <div style="text-align:center;margin:14px 0 4px">
      <a href="{submit_url}" style="color:{RED};font-weight:700;font-size:14px;text-decoration:none">
        + Submit an event for the next digest &rsaquo;</a>
    </div>
  </div>

  <div style="background:{NAVY};padding:22px 28px">
    <div style="color:#fff;font-size:15px;font-weight:700;margin-bottom:10px">We work for you. Reach out to us anytime about anything.</div>
    <table cellpadding="0" cellspacing="0" style="border-collapse:collapse">{committee_html}</table>
    <div style="color:#9fb3d1;font-size:12px;margin-top:8px">chris@maidmentnh.com</div>
  </div>
  <div style="padding:16px 28px;background:#eef1f6;color:{MUTE};font-size:12px;line-height:1.5">
    You're receiving this as a filed Republican candidate for the NH House.
    <a href="{unsub_url}" style="color:{MUTE};text-decoration:underline">Unsubscribe from this weekly digest</a>
    — you'll still receive other committee communications.
  </div>
</div></body></html>"""


def render_digest_text(intro, events, unsub_url):
    lines = [(intro or '').strip(), '', 'UPCOMING EVENTS & TRAININGS', '']
    if events:
        for e in events:
            head = f"[{e['category']}] {e['title']}"
            lines.append(head)
            meta = ' | '.join(x for x in [_fmt_date(e.get('event_date')), e.get('event_time') or '',
                                          e.get('location') or ''] if x)
            if meta:
                lines.append('  ' + meta)
            if e.get('description'):
                lines.append('  ' + e['description'])
            if e.get('url'):
                lines.append('  ' + e['url'])
            lines.append('')
    else:
        lines.append('No events listed this week.')
        lines.append('')
    lines += [
        'Build your website: ' + LINK_WEBSITE,
        'Take a survey: ' + LINK_SURVEYS,
        'Public candidate list: ' + LINK_CANDIDATES,
        'Previous election results: ' + LINK_RESULTS,
        'Submit an event for the next digest: ' + DIGEST_BASE_URL + '/digest/submit',
        '',
        'We work for you. Reach out to us anytime about anything.',
    ]
    for name, title, phone in COMMITTEE:
        lines.append(f'  {name}, {title} — {phone}')
    lines += ['', 'Unsubscribe from this weekly digest (you stay on other lists): ' + unsub_url]
    return '\n'.join(lines)


# --------------------------------------------------------------------------- #
# Sending (threaded so the request doesn't block on hundreds of SES calls)
# --------------------------------------------------------------------------- #
def _send_worker(send_id, subject, intro, events, recipients):
    import boto3
    ses = boto3.client(
        'ses', region_name=os.environ.get('AWS_REGION', 'us-east-1'),
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'))
    sent = failed = 0
    for name, email in recipients:
        try:
            unsub = f"{DIGEST_BASE_URL}/digest/unsubscribe?u={_unsub_token(email)}"
            ses.send_email(
                Source=DIGEST_FROM,
                Destination={'ToAddresses': [email]},
                Message={'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                         'Body': {'Text': {'Data': render_digest_text(intro, events, unsub), 'Charset': 'UTF-8'},
                                  'Html': {'Data': render_digest_html(intro, events, unsub), 'Charset': 'UTF-8'}}},
                ReplyToAddresses=[DIGEST_REPLYTO])
            sent += 1
        except Exception:
            failed += 1
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""UPDATE digest_sends SET sent_count=%s, failed_count=%s,
                       status='complete', finished_at=NOW() WHERE id=%s""", (sent, failed, send_id))
        eids = [e['id'] for e in events if e.get('id')]
        if eids:
            cur.execute("UPDATE digest_events SET status='sent' WHERE id = ANY(%s)", (eids,))
        conn.commit()
    finally:
        cur.close()
        _release_db(conn)


# --------------------------------------------------------------------------- #
# Admin routes
# --------------------------------------------------------------------------- #
def _whoami():
    return getattr(current_user, 'email', None) or 'admin'


@digest_bp.route('/private/digest')
@require_feature_access('digest')
def digest_home():
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT id,title,category,event_date,event_time,location,url,description,
                       status,submitted_by_name,submitted_by_email,created_at
                       FROM digest_events WHERE status IN ('approved','pending')
                       ORDER BY status DESC, event_date NULLS LAST, created_at""")
        cols = ['id','title','category','event_date','event_time','location','url','description',
                'status','submitted_by_name','submitted_by_email','created_at']
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        approved = [r for r in rows if r['status'] == 'approved']
        pending = [r for r in rows if r['status'] == 'pending']
        recips = _recipients(cur)
        cur.execute("SELECT COUNT(*) FROM digest_unsubscribes")
        unsub_n = cur.fetchone()[0]
        cur.execute("""SELECT subject,recipient_count,sent_count,failed_count,status,started_at
                       FROM digest_sends ORDER BY started_at DESC LIMIT 8""")
        sends = cur.fetchall()
    finally:
        cur.close()
        _release_db(conn)
    default_subject = 'Win the House — Weekly Digest, ' + date.today().strftime('%B %-d')
    return render_template('private/digest.html', approved=approved, pending=pending,
                           recipient_count=len(recips), unsub_count=unsub_n, sends=sends,
                           categories=CATEGORIES, default_subject=default_subject)


@digest_bp.route('/private/digest/event/add', methods=['POST'])
@require_feature_access('digest')
def digest_event_add():
    f = request.form
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO digest_events
            (title,category,event_date,event_time,location,url,description,status,reviewed_by,reviewed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'approved',%s,NOW())""",
            (f.get('title','').strip(), f.get('category','Event'),
             f.get('event_date') or None, f.get('event_time','').strip(),
             f.get('location','').strip(), f.get('url','').strip(),
             f.get('description','').strip(), _whoami()))
        conn.commit()
        flash('Event added to the digest.', 'success')
    finally:
        cur.close()
        _release_db(conn)
    return redirect(url_for('digest.digest_home'))


@digest_bp.route('/private/digest/event/<int:eid>/<action>', methods=['POST'])
@require_feature_access('digest')
def digest_event_action(eid, action):
    status = {'approve': 'approved', 'reject': 'rejected', 'archive': 'archived'}.get(action)
    conn = _get_db()
    cur = conn.cursor()
    try:
        if action == 'delete':
            cur.execute("DELETE FROM digest_events WHERE id=%s", (eid,))
        elif status:
            cur.execute("""UPDATE digest_events SET status=%s, reviewed_by=%s, reviewed_at=NOW()
                           WHERE id=%s""", (status, _whoami(), eid))
        conn.commit()
    finally:
        cur.close()
        _release_db(conn)
    return redirect(url_for('digest.digest_home'))


@digest_bp.route('/private/digest/preview')
@require_feature_access('digest')
def digest_preview():
    conn = _get_db()
    cur = conn.cursor()
    try:
        events = _load_approved(cur)
    finally:
        cur.close()
        _release_db(conn)
    intro = request.args.get('intro', 'Here\'s what\'s coming up this week. Reach out anytime — we work for you.')
    return render_digest_html(intro, events, DIGEST_BASE_URL + '/digest/unsubscribe?u=PREVIEW')


def _load_approved(cur):
    cur.execute("""SELECT id,title,category,event_date,event_time,location,url,description
                   FROM digest_events WHERE status='approved'
                   ORDER BY event_date NULLS LAST, created_at""")
    cols = ['id','title','category','event_date','event_time','location','url','description']
    return [dict(zip(cols, r)) for r in cur.fetchall()]


@digest_bp.route('/private/digest/send', methods=['POST'])
@require_feature_access('digest')
def digest_send():
    subject = request.form.get('subject', '').strip() or ('Win the House — Weekly Digest, ' + date.today().strftime('%B %-d'))
    intro = request.form.get('intro', '').strip()
    conn = _get_db()
    cur = conn.cursor()
    try:
        events = _load_approved(cur)
        recipients = _recipients(cur)
        if not recipients:
            flash('No recipients found.', 'error')
            return redirect(url_for('digest.digest_home'))
        cur.execute("""INSERT INTO digest_sends (subject,intro,event_ids,recipient_count,sent_by)
                       VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (subject, intro, [e['id'] for e in events], len(recipients), _whoami()))
        send_id = cur.fetchone()[0]
        conn.commit()
    finally:
        cur.close()
        _release_db(conn)
    threading.Thread(target=_send_worker,
                     args=(send_id, subject, intro, events, recipients), daemon=True).start()
    flash(f'Digest is sending to {len(recipients)} candidates. (Sends run in the background.)', 'success')
    return redirect(url_for('digest.digest_home'))


# --------------------------------------------------------------------------- #
# Public routes (no login)
# --------------------------------------------------------------------------- #
@digest_bp.route('/digest/submit', methods=['GET', 'POST'])
def digest_submit():
    if request.method == 'POST':
        f = request.form
        if not f.get('title', '').strip():
            flash('Please give the event a title.', 'error')
            return redirect(url_for('digest.digest_submit'))
        conn = _get_db()
        cur = conn.cursor()
        try:
            cur.execute("""INSERT INTO digest_events
                (title,category,event_date,event_time,location,url,description,
                 submitted_by_name,submitted_by_email,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
                (f.get('title','').strip(), f.get('category','Event'),
                 f.get('event_date') or None, f.get('event_time','').strip(),
                 f.get('location','').strip(), f.get('url','').strip(),
                 f.get('description','').strip(), f.get('name','').strip(),
                 f.get('email','').strip()))
            conn.commit()
        finally:
            cur.close()
            _release_db(conn)
        return render_template('digest_submit.html', categories=CATEGORIES, submitted=True)
    return render_template('digest_submit.html', categories=CATEGORIES, submitted=False)


@digest_bp.route('/digest/unsubscribe')
def digest_unsubscribe():
    token = request.args.get('u', '')
    if token == 'PREVIEW':
        return render_template('digest_unsub.html', email='you@example.com', resubscribed=False, preview=True)
    try:
        email = _serializer().loads(token)
    except BadSignature:
        abort(404)
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO digest_unsubscribes (email,source) VALUES (%s,'link')
                       ON CONFLICT (email) DO NOTHING""", (email.lower(),))
        conn.commit()
    finally:
        cur.close()
        _release_db(conn)
    return render_template('digest_unsub.html', email=email, resubscribed=False, preview=False)


@digest_bp.route('/digest/resubscribe')
def digest_resubscribe():
    token = request.args.get('u', '')
    try:
        email = _serializer().loads(token)
    except BadSignature:
        abort(404)
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM digest_unsubscribes WHERE email=%s", (email.lower(),))
        conn.commit()
    finally:
        cur.close()
        _release_db(conn)
    return render_template('digest_unsub.html', email=email, resubscribed=True, preview=False)
