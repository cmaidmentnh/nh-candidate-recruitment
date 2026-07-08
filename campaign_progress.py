"""
Campaign Progress Tracker (/progress) — admin matrix of where every 2026 R State
House candidate is in their campaign. Auto-derives signals we already store
(website, donate page, socials, photo/bio, portal account, voter-list & video
requests, surveys, recent activity) and lets restricted admins check off the few
milestones we don't (fundraising, canvassing, signs, training). Computes a
progress score, stage, and a "falling behind" flag.

Mirrors the surveys feature: restricted-allowlist gate, big filterable matrix,
inline-edit JSON endpoints guarded by the X-CSRFToken header.
"""
import os, time, json, re, unicodedata, urllib.request
from functools import wraps
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user

progress_bp = Blueprint('progress', __name__)

# goppictures video-shoot signups (external API, key in env). Cached ~5 min; fails soft.
GOPPICTURES_URL = 'https://www.goppictures.com/api/export'
_GP_CACHE = {'ts': 0.0, 'data': None}


def _norm_name(s):
    s = ''.join(c for c in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(c)).lower()
    return re.sub(r'\s+', ' ', re.sub(r"[^a-z ]", ' ', s)).strip()


def _fetch_goppictures():
    """Return a list of {email, name, type, slot, date, checkin} reservations, or []
    if the key is unset / the API is unreachable. Never raises (progress must load)."""
    key = os.environ.get('GOPPICTURES_API_KEY')
    if not key:
        return []
    now = time.time()
    if _GP_CACHE['data'] is not None and (now - _GP_CACHE['ts']) < 300:
        return _GP_CACHE['data']
    try:
        req = urllib.request.Request(GOPPICTURES_URL, headers={'X-API-Key': key})
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
        out = []
        for r in (payload.get('reservations') or []):
            f = r.get('fields') or {}
            out.append({'email': (f.get('email') or '').strip().lower(),
                        'name': _norm_name(f.get('fullName') or ''),
                        'type': r.get('type'), 'slot': r.get('slotLabel'),
                        'date': r.get('date'), 'checkin': bool(r.get('checkin'))})
        _GP_CACHE['data'] = out
        _GP_CACHE['ts'] = now
        return out
    except Exception:
        return _GP_CACHE['data'] or []

# wired by init_campaign_progress()
_get_db = None
_release_db = None
_is_super_admin = None

# Restricted, like /surveys. Super admin (Chris) is always allowed; add internal
# leadership here. Kept intentionally small — this is sensitive campaign intel.
PROGRESS_ACCESS_EMAILS = {
    'jason@osborne4nh.com',   # Jason Osborne
    'sayra@sayralynn.com',    # Sayra DeVito
}

# The milestones that make up a candidate's progress score (each worth 1 point).
# 'filed' is implicit (100% of the cohort has filed) so it isn't scored.
# 'walkbook' = requested a walkbook via the portal OR an admin ticked the manual box
# (covers voter lists emailed out, which aren't otherwise logged).
AUTO_ITEMS = ['website', 'donate', 'socials', 'photo', 'bio',
              'portal', 'walkbook', 'consult', 'videoshoot', 'survey']
MANUAL_ITEMS = ['fundraising', 'canvassing_started', 'signs_ordered', 'training_attended']
SCORED_ITEMS = AUTO_ITEMS + MANUAL_ITEMS

STAGES = [(0, 'Not started'), (1, 'Getting set up'), (26, 'Building'),
          (51, 'Active'), (76, 'Strong')]

MANUAL_FIELDS = {'fundraising_started', 'fundraising_amount', 'canvassing_started',
                 'signs_ordered', 'training_attended', 'walkbook_done', 'consult_done',
                 'stage_override', 'notes'}
BOOL_FIELDS = {'fundraising_started', 'canvassing_started', 'signs_ordered',
               'training_attended', 'walkbook_done', 'consult_done'}


def init_campaign_progress(get_db, release_db, is_super_admin):
    global _get_db, _release_db, _is_super_admin
    _get_db, _release_db, _is_super_admin = get_db, release_db, is_super_admin


def can_access_progress():
    if _is_super_admin and _is_super_admin():
        return True
    email = getattr(current_user, 'email', None) if current_user.is_authenticated else None
    return bool(email) and email.lower() in PROGRESS_ACCESS_EMAILS


def progress_access_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not current_user.is_authenticated:
            flash("Please log in.", "warning")
            return redirect(url_for('login'))
        if not can_access_progress():
            flash("You don't have access to this page.", "danger")
            return redirect(url_for('index'))
        return f(*a, **k)
    return wrapper


def _stage_for(score_pct):
    label = STAGES[0][1]
    for threshold, name in STAGES:
        if score_pct >= threshold:
            label = name
    return label


def _dkey(d):
    """Natural district sort: 'Cheshire 2' < 'Cheshire 10'."""
    d = (d or '~').strip()
    parts = d.rsplit(' ', 1)
    if len(parts) == 2 and parts[1].isdigit():
        return (parts[0].lower(), int(parts[1]))
    return (d.lower(), 0)


@progress_bp.route('/progress')
@progress_access_required
def progress():
    conn = _get_db()
    cur = conn.cursor()
    try:
        # Base roster: every filed 2026 R State Rep candidate, plus their asset columns.
        cur.execute("""
            SELECT f.candidate_id, f.first_name, f.last_name, f.district_code, f.filed_at,
                   COALESCE(c.incumbent, false),
                   c.external_campaign_url, c.website_url, c.donate_url,
                   c.facebook_url, c.twitter_url, c.instagram_url, c.tiktok_url, c.youtube_url,
                   c.facebook, c.instagram, c.twitter_x,
                   c.photo_url, c.bio, c.password_hash, c.last_login,
                   c.signal_registered, c.phone1_type,
                   c.email, c.email1, c.email2
            FROM filings f LEFT JOIN candidates c ON c.candidate_id = f.candidate_id
            WHERE f.election_year=2026 AND f.party='R' AND f.office='State Representative'
            ORDER BY f.last_name, f.first_name
        """)
        base = cur.fetchall()

        # Match goppictures video-shoot reservations to these candidates (by email, then name).
        email2cid, name2cid = {}, {}
        for row in base:
            _cid, _fn, _ln = row[0], row[1], row[2]
            for em in (row[23], row[24], row[25]):
                if em and '@' in em:
                    email2cid.setdefault(em.strip().lower(), _cid)
            name2cid.setdefault(_norm_name(f"{_fn} {_ln}"), _cid)
        videoshoot = {}   # cid -> {types:set, date, slot, checkin}
        for rv in _fetch_goppictures():
            vcid = email2cid.get(rv['email']) or name2cid.get(rv['name'])
            if not vcid:
                continue
            v = videoshoot.setdefault(vcid, {'types': set(), 'date': rv['date'],
                                             'slot': rv['slot'], 'checkin': False})
            if rv['type']:
                v['types'].add(rv['type'])
            if rv['checkin']:
                v['checkin'] = True

        # Website live + donations enabled from the website-builder tables.
        cur.execute("""
            SELECT wc.recruitment_candidate_id,
                   bool_or(ws.status IN ('live','custom_domain_live')) AS live,
                   bool_or(COALESCE(ws.donations_enabled,false)
                           OR COALESCE(ws.stripe_onboarding_complete,false)) AS donate,
                   bool_or(COALESCE(ws.stripe_onboarding_complete,false)) AS stripe
            FROM ws_candidates wc JOIN ws_submissions ws ON ws.candidate_id = wc.id
            WHERE wc.recruitment_candidate_id IS NOT NULL
            GROUP BY wc.recruitment_candidate_id
        """)
        ws = {rid: {'live': live, 'donate': donate, 'stripe': stripe}
              for rid, live, donate, stripe in cur.fetchall()}

        # Actual dollars raised via the site's Stripe donations (succeeded only).
        cur.execute("""
            SELECT wc.recruitment_candidate_id, round(sum(d.amount_cents)/100.0, 2)
            FROM ws_donations d
            JOIN ws_submissions ws ON ws.id = d.submission_id
            JOIN ws_candidates wc ON wc.id = ws.candidate_id
            WHERE wc.recruitment_candidate_id IS NOT NULL AND d.donation_status = 'succeeded'
            GROUP BY wc.recruitment_candidate_id
        """)
        raised = {rid: float(amt) for rid, amt in cur.fetchall()}

        cur.execute("SELECT DISTINCT ON (candidate_id) candidate_id, status FROM walkbook_requests "
                    "WHERE candidate_id IS NOT NULL ORDER BY candidate_id, created_at DESC")
        walk = dict(cur.fetchall())

        cur.execute("SELECT DISTINCT ON (candidate_id) candidate_id, status FROM consult_requests "
                    "WHERE candidate_id IS NOT NULL ORDER BY candidate_id, created_at DESC")
        consult = dict(cur.fetchall())

        # Which survey orgs each candidate has on file (for showing org logos).
        cur.execute("SELECT candidate_id, survey_org FROM candidate_surveys "
                    "WHERE candidate_id IS NOT NULL AND survey_org IS NOT NULL")
        survey_orgs = {}
        for cid, org in cur.fetchall():
            survey_orgs.setdefault(cid, set()).add(org)

        cur.execute("SELECT candidate_id, max(created_at) FROM activity_log "
                    "WHERE candidate_id IS NOT NULL GROUP BY candidate_id")
        activity = dict(cur.fetchall())

        cur.execute("""SELECT candidate_id, fundraising_started, fundraising_amount,
                              canvassing_started, signs_ordered, training_attended,
                              stage_override, notes, walkbook_done, consult_done
                       FROM candidate_campaign_progress""")
        manual = {r[0]: {'fundraising_started': r[1], 'fundraising_amount': r[2],
                         'canvassing_started': r[3], 'signs_ordered': r[4],
                         'training_attended': r[5], 'stage_override': r[6], 'notes': r[7],
                         'walkbook_done': r[8], 'consult_done': r[9]}
                  for r in cur.fetchall()}

        now = datetime.now()
        rows = []
        for (cid, fn, ln, dist, filed_at, inc, ext_url, web_url, donate_url,
             fb_url, tw_url, ig_url, tt_url, yt_url, fb, ig, tx,
             photo, bio, pw_hash, last_login, sig_reg, phone_type,
             _email, _email1, _email2) in base:
            m = manual.get(cid, {})
            wsr = ws.get(cid, {})
            has_link = bool((ext_url or '').strip() or (web_url or '').strip())
            website_live = bool(wsr.get('live'))
            socials = any((v or '').strip() for v in (fb_url, tw_url, ig_url, tt_url, yt_url, fb, ig, tx))
            last_act = activity.get(cid)
            d = {
                'candidate_id': cid, 'name': f"{fn} {ln}".strip(), 'district': dist or '',
                'incumbent': inc, 'filed_at': filed_at,
                'website': website_live or has_link, 'website_live': website_live,
                'donate': bool(wsr.get('donate')) or bool((donate_url or '').strip()),
                'socials': socials,
                'photo': bool((photo or '').strip()), 'bio': bool((bio or '').strip()),
                'portal': pw_hash is not None, 'last_login': last_login,
                'voter_list': cid in walk, 'voter_list_status': walk.get(cid),
                'walkbook_done': bool(m.get('walkbook_done')),
                'walkbook': (cid in walk) or bool(m.get('walkbook_done')),
                'consult_booked': cid in consult, 'consult_status': consult.get(cid),
                'consult_done': bool(m.get('consult_done')),
                'consult': (cid in consult) or bool(m.get('consult_done')),
                'videoshoot': cid in videoshoot,
                'videoshoot_info': videoshoot.get(cid),
                'headshot': 'headshot' in videoshoot.get(cid, {}).get('types', set()),
                'video': 'video' in videoshoot.get(cid, {}).get('types', set()),
                'survey_orgs': sorted(survey_orgs.get(cid, [])),
                'survey': bool(survey_orgs.get(cid)),
                'signal': bool(sig_reg), 'phone_type': phone_type or '',
                'last_activity': last_act,
                # fundraising: auto from Stripe (onboarded or $ raised) OR manual box
                'stripe': bool(wsr.get('stripe')),
                'raised': raised.get(cid),
                'fundraising': (bool(m.get('fundraising_started')) or bool(wsr.get('stripe'))
                                or (raised.get(cid) or 0) > 0),
                # manual milestones
                'fundraising_started': bool(m.get('fundraising_started')),
                'fundraising_amount': m.get('fundraising_amount'),
                'canvassing_started': bool(m.get('canvassing_started')),
                'signs_ordered': bool(m.get('signs_ordered')),
                'training_attended': bool(m.get('training_attended')),
                'stage_override': m.get('stage_override') or '',
                'notes': m.get('notes') or '',
            }
            done = sum(1 for k in SCORED_ITEMS if d.get(k))
            d['score'] = int(round(100 * done / len(SCORED_ITEMS)))
            d['stage'] = d['stage_override'] or _stage_for(d['score'])
            # Falling behind: filed but almost no footprint — no site, not on our
            # tools, no survey, and a low score. Sortable/filterable at-risk list.
            d['falling_behind'] = (not d['website'] and not d['portal']
                                   and not d['survey'] and d['score'] < 30)
            rows.append(d)

        rows.sort(key=lambda x: (_dkey(x['district']),
                                 x['name'].split()[-1].lower() if x['name'] else '',
                                 x['name'].lower()))

        stats = {
            'total': len(rows),
            'website': sum(1 for r in rows if r['website']),
            'donate': sum(1 for r in rows if r['donate']),
            'portal': sum(1 for r in rows if r['portal']),
            'fundraising': sum(1 for r in rows if r['fundraising']),
            'videoshoot': sum(1 for r in rows if r['videoshoot']),
            'behind': sum(1 for r in rows if r['falling_behind']),
        }
        return render_template('campaign_progress.html', rows=rows, stats=stats)
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/progress/update', methods=['POST'])
@progress_access_required
def progress_update():
    data = request.get_json() or {}
    cid = data.get('candidate_id')
    field = data.get('field')
    if not cid or field not in MANUAL_FIELDS:
        return jsonify({'error': 'bad request'}), 400

    raw = data.get('value')
    if field in BOOL_FIELDS:
        val = bool(raw)
    elif field == 'fundraising_amount':
        try:
            val = float(str(raw).replace(',', '').replace('$', '').strip()) if str(raw).strip() else None
        except ValueError:
            return jsonify({'error': 'bad amount'}), 400
    else:
        val = (str(raw).strip() or None) if raw is not None else None

    who = getattr(current_user, 'email', '') or ''
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute(f"""INSERT INTO candidate_campaign_progress (candidate_id, {field}, updated_at, updated_by)
                        VALUES (%s, %s, now(), %s)
                        ON CONFLICT (candidate_id) DO UPDATE
                          SET {field}=EXCLUDED.{field}, updated_at=now(), updated_by=EXCLUDED.updated_by""",
                    (cid, val, who))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)[:120]}), 500
    finally:
        cur.close()
        _release_db(conn)
