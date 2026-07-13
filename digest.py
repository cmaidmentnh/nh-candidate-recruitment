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
import os, re, threading, html as _html
from urllib.parse import quote_plus, urlparse as _urlparse
from datetime import datetime, date
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, current_app, jsonify)
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
DIGEST_REPLYTO = os.environ.get('DIGEST_REPLYTO', 'info@electhouserepublicans.com')
# Where new-event-submission alerts go (comma-separated ok)
DIGEST_NOTIFY = os.environ.get('DIGEST_NOTIFY', 'chris@maidmentnh.com')
ADMIN_REVIEW_URL = os.environ.get('APP_URL', 'https://nhcandidaterecruitment.com') + '/private/digest'

# Resource links highlighted in every digest
LINK_WEBSITE = 'https://sites.winthehouse.gop'
LINK_SURVEYS = 'https://surveys.winthehouse.gop'
LINK_CANDIDATES = 'https://candidates.electhouserepublicans.com'
LINK_RESULTS = 'https://elections.nhhouse.gop'
LINK_CONSULT = DIGEST_BASE_URL + '/consult'

CATEGORIES = ['Event', 'Training', 'Deadline', 'Resource', 'Other']

# Standing campaign-finance reporting deadlines — shown in EVERY digest (past dates auto-drop off)
FILING_DEADLINES = [date(2026, 8, 19), date(2026, 9, 2), date(2026, 9, 16),
                    date(2026, 10, 14), date(2026, 10, 28), date(2026, 11, 25)]

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


def _short_url(u):
    """Friendly short label for a URL, e.g. https://us06web.zoom.us/... -> zoom.us"""
    try:
        h = _urlparse(u).hostname or u
        h = re.sub(r'^www\.', '', h)
        parts = h.split('.')
        return '.'.join(parts[-2:]) if len(parts) > 2 else h
    except Exception:
        return u


def _linkify(s):
    """Escape text, then turn http(s) URLs into clearly-clickable short links (for event descriptions)."""
    s = s or ''
    out, last = [], 0
    for m in re.finditer(r'https?://[^\s]+', s):
        out.append(_esc(s[last:m.start()]))
        u = m.group(0).rstrip('.,;:)”')
        tail = m.group(0)[len(u):]
        label = _short_url(u)
        out.append(
            f'<a href="{_esc(u)}" target="_blank" '
            f'style="color:{RED};font-weight:700;text-decoration:underline">'
            f'{_esc(label)}&nbsp;&#8599;</a>{_esc(tail)}')
        last = m.end()
    out.append(_esc(s[last:]))
    return ''.join(out)


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


def _town(location):
    """Best-effort city/town from a free-text location ('McIntyre Ski Area, Manchester' -> 'Manchester')."""
    if not location:
        return ''
    parts = [p.strip() for p in location.split(',') if p.strip()]
    return parts[-1] if parts else location.strip()


def render_digest_html(intro, events, unsub_url):
    NAVY, RED, INK, MUTE, LINE = '#16263f', '#b4262d', '#1f2733', '#6b7280', '#e8ebf1'
    cat_color = {'Event': NAVY, 'Training': '#147a45', 'Deadline': RED,
                 'Resource': '#6d3bbf', 'Other': MUTE}
    today = date.today().strftime('%B %-d, %Y')
    submit_url = DIGEST_BASE_URL + '/digest/submit'

    # ---- events ----
    ev_html = ''
    if events:
        for e in events:
            cat = e.get('category') or 'Event'
            ccol = cat_color.get(cat, NAVY)
            # left date / category block
            if e.get('event_date'):
                d = e['event_date']
                block = (f'<table cellpadding="0" cellspacing="0" style="border-collapse:separate"><tr>'
                         f'<td style="background:{NAVY};border-radius:10px;width:58px;text-align:center;padding:8px 0">'
                         f'<div style="color:#f0a7a3;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;line-height:1">{d.strftime("%b")}</div>'
                         f'<div style="color:#ffffff;font-size:24px;font-weight:800;line-height:1.15">{d.strftime("%-d")}</div>'
                         f'</td></tr></table>')
            else:
                block = (f'<table cellpadding="0" cellspacing="0" style="border-collapse:separate"><tr>'
                         f'<td style="background:{ccol};border-radius:10px;width:58px;height:50px;text-align:center;padding:6px 3px;vertical-align:middle">'
                         f'<div style="color:#ffffff;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;line-height:1.25">{_esc(cat)}</div>'
                         f'</td></tr></table>')
            title = _esc(e['title'])
            # location: city is the prominent town; venue/address build a maps link
            online = bool(e.get('is_online'))
            city = (e.get('city') or _town(e.get('location')) or '').strip()
            venue = (e.get('venue') or '').strip()
            place = 'Online' if online else city
            full_addr = ', '.join(x for x in [venue, e.get('address'), city,
                                              e.get('state'), e.get('zip')] if x)
            meta_bits = []
            if place:
                if online or not full_addr:
                    meta_bits.append(f'<span style="color:{NAVY};font-weight:700">&#9679; {_esc(place)}</span>')
                else:
                    maps = 'https://www.google.com/maps/search/?api=1&query=' + quote_plus(full_addr)
                    meta_bits.append(f'<a href="{maps}" style="color:{NAVY};font-weight:700;text-decoration:none">&#9679; {_esc(place)}</a>')
            if venue and not online and venue.lower() != place.lower():
                meta_bits.append(f'<span style="color:{MUTE}">{_esc(venue)}</span>')
            if e.get('event_time'):
                meta_bits.append(f'<span style="color:{MUTE}">{_esc(e["event_time"])}</span>')
            meta_html = ('<div style="font-size:13px;margin:5px 0 0">' +
                         ' &nbsp;&middot;&nbsp; '.join(meta_bits) + '</div>') if meta_bits else ''
            desc = (f'<div style="color:{INK};font-size:14px;line-height:1.55;margin:7px 0 0">'
                    f'{_linkify(e["description"])}</div>') if e.get('description') else ''
            if e.get('url'):
                lbl = ('Register' if cat == 'Training'
                       else 'Details' if cat in ('Deadline', 'Resource', 'Other')
                       else 'RSVP')
                cta = (f'<div style="margin:9px 0 0"><a href="{_esc(e["url"])}" '
                       f'style="color:{RED};font-weight:700;font-size:13px;text-decoration:none">{lbl} &rsaquo;</a></div>')
            else:
                cta = ''
            ev_html += (
                f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border-top:1px solid {LINE}">'
                f'<tr><td style="padding:18px 0 0;width:58px;vertical-align:top">{block}</td>'
                f'<td style="padding:18px 0 18px 16px;vertical-align:top">'
                f'<div style="font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:{ccol}">{_esc(cat)}</div>'
                f'<div style="font-size:18px;font-weight:700;color:{NAVY};line-height:1.25;margin:3px 0 0">{title}</div>'
                f'{meta_html}{desc}{cta}</td></tr></table>')
    else:
        ev_html = (f'<div style="color:{MUTE};font-size:14px;font-style:italic;padding:8px 0">'
                   'Nothing on the calendar this week — submit an event below.</div>')

    intro_html = ''.join(f'<p style="margin:0 0 14px">{_esc(p)}</p>'
                         for p in (intro or '').split('\n\n') if p.strip())

    # ---- campaign finance reporting deadlines (upcoming only) ----
    upcoming = [d for d in FILING_DEADLINES if d >= date.today()]
    pills = ''.join(
        f'<span style="display:inline-block;background:#ffffff;border:1px solid #e6b9b6;color:{RED};'
        f'font-weight:700;font-size:13px;padding:5px 11px;border-radius:14px;margin:3px 5px 0 0">'
        f'{d.strftime("%b %-d")}</span>' for d in upcoming)
    deadlines_html = (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#fdf3f2;border:1px solid #f3d6d4;border-radius:12px;margin:4px 0 0">'
        f'<tr><td style="padding:15px 18px">'
        f'<div style="font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:{RED}">&#9873; Campaign Finance Reporting Deadlines</div>'
        f'<div style="margin:7px 0 6px">{pills}</div>'
        f'<div style="color:{MUTE};font-size:12px">Receipts &amp; expenditure reports are due to the NH Secretary of State on these dates.</div>'
        f'</td></tr></table>') if upcoming else ''

    # ---- resource tiles ----
    res = [('Book a Consult with Chris', LINK_CONSULT, 'Request a 1:1 meeting with Chris Maidment'),
           ('Build Your Website', LINK_WEBSITE, 'A free campaign site in minutes'),
           ('Candidate Surveys', LINK_SURVEYS, 'Earn endorsements & support from conservative organizations'),
           ('Candidate List', LINK_CANDIDATES, 'Everyone on the September ballot'),
           ('Past Election Results', LINK_RESULTS, 'District-by-district history')]
    res_html = ''
    for i, (lbl, href, sub) in enumerate(res):
        bord = '' if i == len(res) - 1 else f'border-bottom:1px solid {LINE};'
        res_html += (f'<a href="{href}" style="display:block;text-decoration:none;padding:11px 0;{bord}">'
                     f'<span style="color:{NAVY};font-weight:700;font-size:14px">{lbl} &rsaquo;</span>'
                     f'<span style="display:block;color:{MUTE};font-size:12px;margin-top:1px">{sub}</span></a>')

    # ---- committee footer (2 columns) ----
    cells = ''
    for i, (name, title, phone) in enumerate(COMMITTEE):
        if i % 2 == 0:
            cells += '<tr>' if i else '<tr>'
        cells += (f'<td style="padding:6px 18px 6px 0;vertical-align:top;width:50%">'
                  f'<div style="color:#ffffff;font-weight:700;font-size:14px">{name}</div>'
                  f'<div style="color:#9fb0c8;font-size:12px">{title}</div>'
                  f'<div style="color:#cdd8e8;font-size:13px;margin-top:1px">{phone}</div></td>')
        if i % 2 == 1:
            cells += '</tr>'

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#eceff4">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#eceff4"><tr><td align="center" style="padding:24px 12px">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">

  <tr><td style="height:5px;background:{RED}"></td></tr>
  <tr><td style="background:{NAVY};padding:26px 30px 22px">
    <div style="color:#9fb0c8;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.2em">Committee to Elect House Republicans</div>
    <div style="color:#ffffff;font-size:30px;font-weight:800;letter-spacing:-.01em;margin-top:6px">Weekly Digest</div>
    <div style="color:#9fb0c8;font-size:13px;margin-top:5px">{today}</div>
  </td></tr>

  <tr><td style="padding:24px 30px 6px">
    <div style="color:{INK};font-size:15px;line-height:1.65">{intro_html}</div>
    <div style="font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:{RED};margin:6px 0 0">Upcoming Events &amp; Trainings</div>
    {ev_html}
  </td></tr>

  <tr><td style="padding:14px 30px 2px">{deadlines_html}</td></tr>

  <tr><td style="padding:8px 30px 4px">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f7fb;border-radius:12px">
      <tr><td style="padding:16px 20px">
        <div style="font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:{NAVY};margin-bottom:4px">Tools &amp; Resources</div>
        {res_html}
      </td></tr>
    </table>
  </td></tr>

  <tr><td style="padding:18px 30px 24px;text-align:center">
    <a href="{submit_url}" style="display:inline-block;border:1.5px solid {RED};color:{RED};border-radius:24px;padding:10px 22px;font-weight:700;font-size:14px;text-decoration:none">Have an event? Submit it for next week &rsaquo;</a>
  </td></tr>

  <tr><td style="background:{NAVY};padding:24px 30px">
    <div style="color:#ffffff;font-size:17px;font-weight:800">We work for you.</div>
    <div style="color:#9fb0c8;font-size:13px;margin:2px 0 16px">Reach out anytime, about anything.</div>
    <table width="100%" cellpadding="0" cellspacing="0">{cells}</table>
    <div style="color:#9fb0c8;font-size:12px;margin-top:14px;border-top:1px solid #2a3c57;padding-top:12px">chris@maidmentnh.com</div>
  </td></tr>

  <tr><td style="padding:16px 30px;background:#f5f7fb;color:{MUTE};font-size:12px;line-height:1.5">
    You're receiving this as a filed Republican candidate for the NH House.
    <a href="{unsub_url}" style="color:{MUTE};text-decoration:underline">Unsubscribe from this weekly digest</a> — you'll stay on our list for everything else.
  </td></tr>

</table></td></tr></table></body></html>"""


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
    upcoming = [d for d in FILING_DEADLINES if d >= date.today()]
    if upcoming:
        lines += ['', 'CAMPAIGN FINANCE REPORTING DEADLINES (NH Secretary of State):',
                  '  ' + ' | '.join(d.strftime('%b %-d') for d in upcoming), '']
    lines += [
        'Book a consult with Chris (request a 1:1 meeting): ' + LINK_CONSULT,
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
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import parseaddr
    ses = boto3.client(
        'ses', region_name=os.environ.get('AWS_REGION', 'us-east-1'),
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'))
    source_addr = parseaddr(DIGEST_FROM)[1] or DIGEST_FROM
    sent = failed = 0
    for name, email in recipients:
        try:
            unsub = f"{DIGEST_BASE_URL}/digest/unsubscribe?u={_unsub_token(email)}"
            # Raw MIME so we can set List-Unsubscribe + one-click headers (RFC 8058).
            # These are a major deliverability signal for Gmail/Comcast on bulk mail —
            # without them, providers are far likelier to spam-folder or throttle us.
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = DIGEST_FROM
            msg['To'] = email
            msg['Reply-To'] = DIGEST_REPLYTO
            msg['List-Unsubscribe'] = f"<{unsub}>, <mailto:{DIGEST_REPLYTO}?subject=unsubscribe>"
            msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
            msg.attach(MIMEText(render_digest_text(intro, events, unsub), 'plain', 'utf-8'))
            msg.attach(MIMEText(render_digest_html(intro, events, unsub), 'html', 'utf-8'))
            ses.send_raw_email(Source=source_addr, Destinations=[email],
                               RawMessage={'Data': msg.as_string()})
            sent += 1
        except Exception:
            failed += 1
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""UPDATE digest_sends SET sent_count=%s, failed_count=%s,
                       status='complete', finished_at=NOW() WHERE id=%s""", (sent, failed, send_id))
        # Events intentionally stay 'approved' so they repeat in each edition until
        # their date passes (auto-filtered by _load_approved). Per-send history is
        # preserved in digest_sends.event_ids. Undated events persist until archived.
        conn.commit()
    finally:
        cur.close()
        _release_db(conn)


# --------------------------------------------------------------------------- #
# Admin routes
# --------------------------------------------------------------------------- #
def _notify_submission(f, loc):
    """Email the admin the moment a candidate submits an event. Best-effort (never blocks the submit)."""
    try:
        import boto3
        title = f.get('title', '').strip()
        who = f.get('name', '').strip() or 'someone'
        whom_email = f.get('email', '').strip()
        place = 'Online' if loc.get('is_online') else (loc.get('location') or '—')
        when = ' '.join(x for x in [f.get('event_date', ''), f.get('event_time', '').strip()] if x) or '—'
        body = (f"New event submitted for the weekly digest:\n\n"
                f"  {title}\n"
                f"  Category: {f.get('category','Event')}\n"
                f"  When: {when}\n"
                f"  Where: {place}\n"
                f"  Link: {f.get('url','').strip() or '—'}\n"
                f"  Details: {f.get('description','').strip() or '—'}\n\n"
                f"  Submitted by: {who}" + (f" <{whom_email}>" if whom_email else "") + "\n\n"
                f"Review / approve it here: {ADMIN_REVIEW_URL}")
        ses = boto3.client('ses', region_name=os.environ.get('AWS_REGION', 'us-east-1'),
                           aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                           aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'))
        ses.send_email(
            Source=DIGEST_FROM,
            Destination={'ToAddresses': [a.strip() for a in DIGEST_NOTIFY.split(',') if a.strip()]},
            Message={'Subject': {'Data': f'Digest submission: {title}', 'Charset': 'UTF-8'},
                     'Body': {'Text': {'Data': body, 'Charset': 'UTF-8'}}},
            ReplyToAddresses=[whom_email] if whom_email else [DIGEST_REPLYTO])
    except Exception:
        pass


def _whoami():
    return getattr(current_user, 'email', None) or 'admin'


def _loc_from_form(f):
    """Pull structured location fields from a submitted form. Returns a dict."""
    online = f.get('is_online') in ('on', '1', 'true', 'yes')
    venue = f.get('venue', '').strip()
    address = f.get('address', '').strip()
    city = f.get('city', '').strip()
    state = (f.get('state', '').strip() or 'NH')
    zipc = f.get('zip', '').strip()
    if online:
        composed = 'Online'
    else:
        composed = ', '.join(x for x in [venue, address, city, state, zipc] if x)
    return {'venue': venue, 'address': address, 'city': city, 'state': state,
            'zip': zipc, 'is_online': online, 'location': composed}


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
        _today = date.today()
        approved_all = [r for r in rows if r['status'] == 'approved']
        # Split off past-dated events: they are NOT sent (see _load_approved's
        # event_date >= CURRENT_DATE filter), so showing them as "will be included"
        # is misleading. Evergreen (NULL-date) events always send, so they stay.
        past = [r for r in approved_all if r['event_date'] and r['event_date'] < _today]
        approved = [r for r in approved_all if not (r['event_date'] and r['event_date'] < _today)]
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
    default_subject = 'Committee to Elect House Republicans — Weekly Digest, ' + date.today().strftime('%B %-d')
    return render_template('private/digest.html', approved=approved, pending=pending, past=past,
                           recipient_count=len(recips), unsub_count=unsub_n, sends=sends,
                           categories=CATEGORIES, default_subject=default_subject)


@digest_bp.route('/private/digest/event/add', methods=['POST'])
@require_feature_access('digest')
def digest_event_add():
    f = request.form
    loc = _loc_from_form(f)
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO digest_events
            (title,category,event_date,event_time,location,venue,address,city,state,zip,is_online,
             url,description,status,reviewed_by,reviewed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'approved',%s,NOW())""",
            (f.get('title','').strip(), f.get('category','Event'),
             f.get('event_date') or None, f.get('event_time','').strip(),
             loc['location'], loc['venue'], loc['address'], loc['city'], loc['state'], loc['zip'], loc['is_online'],
             f.get('url','').strip(), f.get('description','').strip(), _whoami()))
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


_EVENT_COLS = ['id','title','category','event_date','event_time','location','url','description',
               'venue','address','city','state','zip','is_online']
_EVENT_SEL = ','.join(_EVENT_COLS)


def _load_approved(cur):
    # Approved events repeat in every weekly edition until their date has passed;
    # undated events persist until an admin archives them. Past-dated events auto-drop.
    cur.execute(f"""SELECT {_EVENT_SEL} FROM digest_events
                    WHERE status='approved'
                      AND (event_date IS NULL OR event_date >= CURRENT_DATE)
                    ORDER BY event_date NULLS LAST, created_at""")
    return [dict(zip(_EVENT_COLS, r)) for r in cur.fetchall()]


@digest_bp.route('/api/public/events')
def public_events():
    """Public, read-only JSON feed of approved upcoming events (no auth, no PII).
    Consumed by the CTEHR website's /events page via a same-origin proxy."""
    conn = _get_db()
    cur = conn.cursor()
    try:
        events = _load_approved(cur)
    finally:
        cur.close()
        _release_db(conn)
    for e in events:
        if e.get('event_date'):
            e['event_date'] = e['event_date'].isoformat()
    resp = jsonify({'ok': True, 'events': events})
    resp.headers['Cache-Control'] = 'public, max-age=300'
    return resp


@digest_bp.route('/private/digest/send', methods=['POST'])
@require_feature_access('digest')
def digest_send():
    subject = request.form.get('subject', '').strip() or ('Committee to Elect House Republicans — Weekly Digest, ' + date.today().strftime('%B %-d'))
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
        loc = _loc_from_form(f)
        conn = _get_db()
        cur = conn.cursor()
        try:
            cur.execute("""INSERT INTO digest_events
                (title,category,event_date,event_time,location,venue,address,city,state,zip,is_online,
                 url,description,submitted_by_name,submitted_by_email,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
                (f.get('title','').strip(), f.get('category','Event'),
                 f.get('event_date') or None, f.get('event_time','').strip(),
                 loc['location'], loc['venue'], loc['address'], loc['city'], loc['state'], loc['zip'], loc['is_online'],
                 f.get('url','').strip(), f.get('description','').strip(),
                 f.get('name','').strip(), f.get('email','').strip()))
            conn.commit()
        finally:
            cur.close()
            _release_db(conn)
        _notify_submission(f, loc)
        return render_template('digest_submit.html', categories=CATEGORIES, submitted=True)
    return render_template('digest_submit.html', categories=CATEGORIES, submitted=False)


@digest_bp.route('/digest/unsubscribe', methods=['GET', 'POST'])
def digest_unsubscribe():
    # POST supports RFC 8058 one-click unsubscribe (mail clients POST here with
    # body "List-Unsubscribe=One-Click"); GET is the human link. Both use ?u=token.
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
