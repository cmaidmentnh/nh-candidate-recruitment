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


def _role():
    return (getattr(current_user, 'role', '') or '').lower() if current_user.is_authenticated else ''


def is_whip():
    """Whips get the mobile check-in workflow but not the desktop matrix."""
    return _role() == 'whip'


def can_access_progress():
    """Desktop matrix: super admin + the named leadership allowlist."""
    if _is_super_admin and _is_super_admin():
        return True
    email = getattr(current_user, 'email', None) if current_user.is_authenticated else None
    return bool(email) and email.lower() in PROGRESS_ACCESS_EMAILS


def can_whip():
    """Whip screens: anyone who can see the matrix, plus role='whip'."""
    return can_access_progress() or is_whip()


def whip_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not current_user.is_authenticated:
            flash("Please log in.", "warning")
            return redirect(url_for('login'))
        if not can_whip():
            flash("You don't have access to this page.", "danger")
            return redirect(url_for('index'))
        return f(*a, **k)
    return wrapper


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


def _build_rows(cur):
    """Every filed 2026 R State Rep candidate with their derived signals.

    Extracted from the /progress route so the whip screens work off exactly the
    same data as the desktop matrix instead of a second, drifting copy.
    """
    if True:
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
        return rows


def _stats_for(rows):
    return {
        'total': len(rows),
        'website': sum(1 for r in rows if r['website']),
        'donate': sum(1 for r in rows if r['donate']),
        'portal': sum(1 for r in rows if r['portal']),
        'fundraising': sum(1 for r in rows if r['fundraising']),
        'videoshoot': sum(1 for r in rows if r['videoshoot']),
        'behind': sum(1 for r in rows if r['falling_behind']),
    }


@progress_bp.route('/progress')
@progress_access_required
def progress():
    """Desktop matrix. Unchanged - kept for Chris and leadership."""
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows = _build_rows(cur)
        return render_template('campaign_progress.html', rows=rows,
                               stats=_stats_for(rows))
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


# =====================================================================
# Whip workflow — mobile-first. Three screens: my list, candidate, check-in.
# =====================================================================

METHODS = ['call', 'text', 'email', 'in_person', 'voicemail']
ANSWERS = ['yes', 'not_yet', 'planning']
NEEDS = ['walkbook', 'video', 'palm_cards', 'website', 'yard_signs', 'training', 'other']


def _uid():
    """The integer users.user_id for the signed-in admin/whip.

    AdminUser.id is the flask-login key "u_12", not an integer - int() on it
    throws, which would have left every whip's assigned list silently empty.
    The real column is exposed as .user_id.
    """
    uid = getattr(current_user, 'user_id', None)
    if uid is None:
        raw = str(getattr(current_user, 'id', '') or '')
        uid = raw[2:] if raw.startswith('u_') else raw
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


def _whips(cur):
    """Accounts that can be assigned candidates: whips and admins."""
    cur.execute("""SELECT user_id, COALESCE(username, email) FROM users
                   WHERE role IN ('whip','admin') ORDER BY 2""")
    return [{'user_id': r[0], 'name': r[1]} for r in cur.fetchall()]


def _attach_whip_state(cur, rows):
    """Merge assignment + last-contact onto the shared row set."""
    cur.execute("""
        SELECT candidate_id, assigned_whip, last_contact_at, next_followup_at, needs
        FROM candidate_campaign_progress
    """)
    state = {r[0]: r for r in cur.fetchall()}
    cur.execute("SELECT user_id, COALESCE(username, email) FROM users")
    names = dict(cur.fetchall())
    for d in rows:
        st = state.get(d['candidate_id'])
        d['assigned_whip'] = st[1] if st else None
        d['assigned_name'] = names.get(st[1]) if st and st[1] else None
        d['last_contact_at'] = st[2] if st else None
        d['next_followup_at'] = st[3] if st else None
        d['needs'] = list(st[4]) if st and st[4] else []
    return rows


def _gaps(d):
    """The two or three things this candidate is missing — the call script."""
    out = []
    if not d.get('website'):    out.append('No website')
    if not d.get('portal'):     out.append('Portal account not activated')
    if not d.get('survey'):     out.append('Survey not returned')
    if not d.get('walkbook'):   out.append('No walkbook requested')
    if not d.get('photo'):      out.append('No photo')
    if not d.get('donate'):     out.append('No donate link')
    if not d.get('fundraising'): out.append('Fundraising not started')
    if not d.get('canvassing_started'): out.append('Not door-knocking yet')
    return out


def _priority(d, uid):
    """Lower sorts first. Chris's rule: if it isn't done, it's overdue —
    so there is no grace period, only 'never talked to' before 'talked to'."""
    mine = 0 if d.get('assigned_whip') == uid else 1
    never = 0 if not d.get('last_contact_at') else 1
    return (mine, never, -len(_gaps(d)), d.get('last_contact_at') or datetime.min)


@progress_bp.route('/whip')
@whip_required
def whip_list():
    """Screen A — my list first, then everyone else."""
    uid = _uid()
    view = request.args.get('view', 'need')
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows = _attach_whip_state(cur, _build_rows(cur))
        for d in rows:
            d['gaps'] = _gaps(d)
        rows.sort(key=lambda d: _priority(d, uid))
        mine = [d for d in rows if d.get('assigned_whip') == uid]
        if view == 'mine':
            shown = mine
        elif view == 'never':
            shown = [d for d in rows if not d.get('last_contact_at')]
        elif view == 'need':
            shown = [d for d in rows if not d.get('last_contact_at') or d['gaps']]
        else:
            shown = rows
        return render_template('whip_list.html', rows=shown, view=view,
                               mine_count=len(mine), total=len(rows), uid=uid)
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/c/<int:candidate_id>')
@whip_required
def whip_candidate(candidate_id):
    """Screen B — one candidate, contact buttons, the ask, recent check-ins."""
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows = _attach_whip_state(cur, _build_rows(cur))
        d = next((r for r in rows if r['candidate_id'] == candidate_id), None)
        if not d:
            flash("Candidate not found in the 2026 filed cohort.", "warning")
            return redirect(url_for('progress.whip_list'))
        d['gaps'] = _gaps(d)
        cur.execute("""
            SELECT id, contacted_at, contacted_by_name, method, reached, notes,
                   needs, contacted_by
            FROM campaign_checkins WHERE candidate_id = %s
            ORDER BY contacted_at DESC LIMIT 10
        """, (candidate_id,))
        checkins = [{'id': r[0], 'at': r[1], 'by': r[2], 'method': r[3],
                     'reached': r[4], 'notes': r[5],
                     'needs': list(r[6]) if r[6] else [],
                     'can_edit': _may_edit_checkin(r[7])}
                    for r in cur.fetchall()]
        cur.execute("SELECT phone1, phone2, email, email1 FROM candidates WHERE candidate_id=%s",
                    (candidate_id,))
        c = cur.fetchone() or (None, None, None, None)
        contact = {'phone': c[0] or c[1], 'email': c[2] or c[3]}
        return render_template('whip_candidate.html', d=d, checkins=checkins,
                               contact=contact, methods=METHODS,
                               answers=ANSWERS, needs_options=NEEDS,
                               whips=_whips(cur))
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/checkin', methods=['POST'])
@whip_required
def whip_checkin():
    """Screen C — log a conversation. Milestones are captured here as a
    by-product of the call, which is why the standalone checkboxes stayed empty."""
    f = request.form
    try:
        cid = int(f.get('candidate_id') or 0)
    except ValueError:
        cid = 0
    if not cid:
        return jsonify({'ok': False, 'error': 'missing candidate'}), 400

    method = f.get('method') if f.get('method') in METHODS else None
    reached = f.get('reached') == 'yes'
    notes = (f.get('notes') or '').strip() or None
    needs = [n for n in f.getlist('needs') if n in NEEDS]
    ans = {k: (f.get(k) if f.get(k) in ANSWERS else None)
           for k in ('fundraising', 'canvassing', 'signs', 'training')}

    uid = _uid()
    uname = (getattr(current_user, 'username', None)
             or getattr(current_user, 'email', None) or 'unknown')

    followup = None
    days = f.get('followup')
    if days and days.isdigit():
        followup = (datetime.utcnow() + timedelta(days=int(days))).date()

    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO campaign_checkins
                (candidate_id, contacted_by, contacted_by_name, method, reached,
                 needs, notes, fundraising, canvassing, signs, training)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (cid, uid, uname, method, reached, needs or None, notes,
              ans['fundraising'], ans['canvassing'], ans['signs'], ans['training']))

        # Roll the answers up into current state. 'yes' sets the flag; a later
        # 'not_yet' can clear it, so the tracker reflects the last thing we heard.
        #
        # The columns must appear in the INSERT as well as the DO UPDATE: most
        # candidates have no progress row yet, so the insert succeeds, the
        # conflict clause never fires, and putting the values only in DO UPDATE
        # silently discarded every answer on a candidate's first check-in.
        cols = ['candidate_id', 'last_contact_at', 'updated_by', 'updated_at']
        ph   = ['%s', 'now()', '%s', 'now()']
        vals = [cid, uname]

        for form_key, col in (('fundraising', 'fundraising_started'),
                              ('canvassing', 'canvassing_started'),
                              ('signs', 'signs_ordered'),
                              ('training', 'training_attended')):
            if ans[form_key] in ('yes', 'not_yet'):
                cols.append(col); ph.append('%s')
                vals.append(ans[form_key] == 'yes')
        if needs:
            cols.append('needs'); ph.append('%s'); vals.append(needs)
        if followup:
            cols.append('next_followup_at'); ph.append('%s'); vals.append(followup)

        updates = ', '.join(f"{c} = EXCLUDED.{c}" for c in cols if c != 'candidate_id')
        cur.execute(f"""
            INSERT INTO candidate_campaign_progress ({', '.join(cols)})
            VALUES ({', '.join(ph)})
            ON CONFLICT (candidate_id) DO UPDATE SET {updates}
        """, vals)
        conn.commit()
        flash("Check-in saved.", "success")
        return redirect(url_for('progress.whip_candidate', candidate_id=cid))
    except Exception as e:
        conn.rollback()
        flash(f"Could not save the check-in: {e}", "danger")
        return redirect(url_for('progress.whip_candidate', candidate_id=cid))
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/assign', methods=['POST'])
@progress_access_required
def whip_assign():
    """Admin only — Chris assigns candidates to whips."""
    f = request.form
    try:
        cid = int(f.get('candidate_id') or 0)
    except ValueError:
        cid = 0
    whip = f.get('assigned_whip')
    whip = int(whip) if (whip or '').isdigit() else None
    if not cid:
        return jsonify({'ok': False, 'error': 'missing candidate'}), 400
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO candidate_campaign_progress (candidate_id, assigned_whip)
            VALUES (%s, %s)
            ON CONFLICT (candidate_id) DO UPDATE SET assigned_whip = EXCLUDED.assigned_whip
        """, (cid, whip))
        conn.commit()
        flash("Assignment saved.", "success")
        return redirect(url_for('progress.whip_candidate', candidate_id=cid))
    finally:
        cur.close()
        _release_db(conn)


def _may_edit_checkin(row_contacted_by):
    """You can fix your own check-in; admins can fix anyone's."""
    return can_access_progress() or (row_contacted_by is not None
                                     and row_contacted_by == _uid())


@progress_bp.route('/whip/checkin/<int:checkin_id>/edit', methods=['POST'])
@whip_required
def whip_checkin_edit(checkin_id):
    """Correct a mis-tapped check-in. Without this a wrong chip is permanent,
    which is enough on its own to stop people using the tool."""
    f = request.form
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT candidate_id, contacted_by FROM campaign_checkins WHERE id=%s",
                    (checkin_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'not found'}), 404
        cid, by = row
        if not _may_edit_checkin(by):
            return jsonify({'ok': False, 'error': 'not yours'}), 403

        sets, vals = [], []
        if f.get('method') in METHODS:
            sets.append("method = %s"); vals.append(f.get('method'))
        if f.get('reached') in ('yes', 'no'):
            sets.append("reached = %s"); vals.append(f.get('reached') == 'yes')
        if 'notes' in f:
            sets.append("notes = %s"); vals.append((f.get('notes') or '').strip() or None)
        if not sets:
            return jsonify({'ok': True})
        cur.execute(f"UPDATE campaign_checkins SET {', '.join(sets)} WHERE id=%s",
                    vals + [checkin_id])
        conn.commit()
        return jsonify({'ok': True})
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/checkin/<int:checkin_id>/delete', methods=['POST'])
@whip_required
def whip_checkin_delete(checkin_id):
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT candidate_id, contacted_by FROM campaign_checkins WHERE id=%s",
                    (checkin_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'not found'}), 404
        cid, by = row
        if not _may_edit_checkin(by):
            return jsonify({'ok': False, 'error': 'not yours'}), 403

        cur.execute("DELETE FROM campaign_checkins WHERE id=%s", (checkin_id,))
        # last_contact_at must fall back to the newest surviving check-in, or
        # clear entirely if that was the only one - otherwise a deleted entry
        # leaves the candidate looking contacted.
        cur.execute("""
            UPDATE candidate_campaign_progress SET last_contact_at =
                (SELECT MAX(contacted_at) FROM campaign_checkins WHERE candidate_id=%s)
            WHERE candidate_id=%s
        """, (cid, cid))
        conn.commit()
        flash("Check-in deleted.", "success")
        return redirect(url_for('progress.whip_candidate', candidate_id=cid))
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/dashboard')
@whip_required
def whip_dashboard():
    """Coverage at a glance: how much of the field has been worked, by whom."""
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows = _attach_whip_state(cur, _build_rows(cur))
        for d in rows:
            d['gaps'] = _gaps(d)
        total = len(rows)
        contacted = sum(1 for d in rows if d['last_contact_at'])
        assigned = sum(1 for d in rows if d['assigned_whip'])
        behind = sum(1 for d in rows if d['falling_behind'])

        cur.execute("""
            SELECT COALESCE(u.username, u.email, 'unassigned') AS who,
                   COUNT(*) AS assigned,
                   COUNT(p.last_contact_at) AS contacted
            FROM candidate_campaign_progress p
            LEFT JOIN users u ON u.user_id = p.assigned_whip
            WHERE p.assigned_whip IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC
        """)
        per_whip = [{'who': r[0], 'assigned': r[1], 'contacted': r[2],
                     'pct': int(round(100 * r[2] / r[1])) if r[1] else 0}
                    for r in cur.fetchall()]

        cur.execute("""
            SELECT c.contacted_at, c.contacted_by_name, c.method, c.reached,
                   cand.first_name, cand.last_name
            FROM campaign_checkins c
            JOIN candidates cand ON cand.candidate_id = c.candidate_id
            ORDER BY c.contacted_at DESC LIMIT 15
        """)
        recent = [{'at': r[0], 'by': r[1], 'method': r[2], 'reached': r[3],
                   'name': f"{r[4]} {r[5]}".strip()} for r in cur.fetchall()]

        cur.execute("SELECT COUNT(*) FROM campaign_checkins")
        checkins = cur.fetchone()[0]

        stats = {'total': total, 'contacted': contacted,
                 'never': total - contacted, 'assigned': assigned,
                 'unassigned': total - assigned, 'behind': behind,
                 'checkins': checkins,
                 'pct': int(round(100 * contacted / total)) if total else 0}
        return render_template('whip_dashboard.html', stats=stats,
                               per_whip=per_whip, recent=recent)
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/export.csv')
@progress_access_required
def whip_export():
    """Everything a whip has recorded, as CSV. Admin only."""
    import csv as _csv, io
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT cand.first_name, cand.last_name, f.district_code,
                   k.contacted_at, k.contacted_by_name, k.method, k.reached,
                   k.fundraising, k.canvassing, k.signs, k.training,
                   array_to_string(k.needs, '; '), k.notes
            FROM campaign_checkins k
            JOIN candidates cand ON cand.candidate_id = k.candidate_id
            LEFT JOIN filings f ON f.candidate_id = k.candidate_id
                 AND f.election_year = 2026 AND f.office = 'State Representative'
            ORDER BY k.contacted_at DESC
        """)
        buf = io.StringIO()
        w = _csv.writer(buf)
        w.writerow(['first_name', 'last_name', 'district', 'contacted_at',
                    'contacted_by', 'method', 'reached', 'fundraising',
                    'canvassing', 'signs', 'training', 'needs', 'notes'])
        for r in cur.fetchall():
            w.writerow(r)
        from flask import Response
        return Response(buf.getvalue(), mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename=whip_checkins.csv'})
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/mine')
@whip_required
def whip_mine():
    """Just your assignments, with your own completion bar."""
    uid = _uid()
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows = _attach_whip_state(cur, _build_rows(cur))
        rows = [d for d in rows if d.get('assigned_whip') == uid]
        for d in rows:
            d['gaps'] = _gaps(d)
        rows.sort(key=lambda d: _priority(d, uid))
        done = sum(1 for d in rows if d['last_contact_at'])
        pct = int(round(100 * done / len(rows))) if rows else 0
        return render_template('whip_mine.html', rows=rows, done=done, pct=pct)
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/tasks')
@whip_required
def whip_tasks():
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT t.id, t.title, t.due_date, t.done, t.done_at,
                   COALESCE(u.username, u.email),
                   CASE WHEN c.candidate_id IS NULL THEN NULL
                        ELSE c.first_name || ' ' || c.last_name END
            FROM whip_tasks t
            LEFT JOIN users u ON u.user_id = t.assigned_to
            LEFT JOIN candidates c ON c.candidate_id = t.candidate_id
            ORDER BY t.done, COALESCE(t.due_date, '2099-12-31'), t.created_at DESC
        """)
        all_t = [{'id': r[0], 'title': r[1], 'due_date': r[2], 'done': r[3],
                  'done_at': r[4], 'assigned_name': r[5], 'cand_name': r[6]}
                 for r in cur.fetchall()]
        return render_template('whip_tasks.html',
                               open_tasks=[t for t in all_t if not t['done']],
                               done_tasks=[t for t in all_t if t['done']],
                               whips=_whips(cur))
    finally:
        cur.close()
        _release_db(conn)


@progress_bp.route('/whip/tasks/new', methods=['POST'])
@whip_required
def whip_task_new():
    f = request.form
    title = (f.get('title') or '').strip()
    if not title:
        flash("A task needs a title.", "warning")
        return redirect(url_for('progress.whip_tasks'))
    assigned = f.get('assigned_to')
    assigned = int(assigned) if (assigned or '').isdigit() else None
    due = f.get('due_date') or None
    uname = (getattr(current_user, 'username', None)
             or getattr(current_user, 'email', None) or 'unknown')
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO whip_tasks (title, assigned_to, due_date, created_by, created_by_name)
            VALUES (%s,%s,%s,%s,%s)
        """, (title, assigned, due, _uid(), uname))
        conn.commit()
        flash("Task added.", "success")
    finally:
        cur.close()
        _release_db(conn)
    return redirect(url_for('progress.whip_tasks'))


@progress_bp.route('/whip/tasks/<int:task_id>/toggle', methods=['POST'])
@whip_required
def whip_task_toggle(task_id):
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE whip_tasks
               SET done = NOT done,
                   done_at = CASE WHEN done THEN NULL ELSE now() END
             WHERE id = %s
        """, (task_id,))
        conn.commit()
    finally:
        cur.close()
        _release_db(conn)
    return redirect(url_for('progress.whip_tasks'))
