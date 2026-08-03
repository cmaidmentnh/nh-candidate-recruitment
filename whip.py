"""
Candidate whip — weekly check-in calls on assigned candidates.

A whip owns a handful of the 365 filed 2026 R State Rep candidates and rings
each of them once a week. The point of the call is to find out what the
candidate needs, so the tool is built around three things:

  KNOW BEFORE YOU DIAL   The call screen is a dossier first and a form second.
                         Everything we already track — website, donate page,
                         socials, portal account, walkbook, video shoot,
                         survey, dollars raised, who else is on that ballot,
                         what they asked for last week — is on the page above
                         the questions, so the whip opens with "did that
                         walkbook ever show up?" instead of "so, how's it
                         going?".

  THE WEEK IS THE ROUND  No admin opens or closes anything. Monday morning the
                         whole roster is due again. Progress is "6 of 9 done
                         this week", which is finite and finishable.

  FIX IT ON THE CALL     Anything the whip learns is wrong gets corrected in
                         place, written through to the master candidate record
                         so every other tool gets it too, and logged.

    /whip                my week
    /whip/c/<id>         dossier + this week's check-in
    /whip/c/<id>/fix     correct a tracked field                    (JSON)
    /whip/needs          what candidates asked for                  (admin)
    /whip/assign         who owns whom                              (admin)
    /whip/whips          whip roster + coverage                     (admin)
    /whip/export.csv     this week, flat                            (admin)
"""
import io
import csv as _csv
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, Response)
from flask_login import current_user

whip_bp = Blueprint('whip', __name__)
_get_db = _release_db = _build_rows = _can_admin = None


def init_whip(get_db, release_db, build_rows, can_admin, upload_file=None):
    global _get_db, _release_db, _build_rows, _can_admin
    _get_db, _release_db, _build_rows, _can_admin = (get_db, release_db,
                                                     build_rows, can_admin)


# ------------------------------------------------------------- the ask
# How the call went. "No answer" is a real outcome worth recording — three
# weeks of no answer is itself the finding.
OUTCOMES = [('talked', 'Talked'), ('voicemail', 'Left voicemail'),
            ('texted', 'Texted'), ('emailed', 'Emailed'),
            ('no_answer', "Couldn't reach")]

# Where the race stands, in the whip's judgement. One question, four answers,
# and it is the column leadership reads first.
STATUSES = [('rolling', 'Rolling along'), ('needs_help', 'Needs help'),
            ('at_risk', 'In trouble'), ('not_running', 'Not really running')]

# The whole reason for the call. Everything here is something we can actually
# deliver, so an ask becomes a work order on /whip/needs.
ASKS = [('walkbook', 'Voter list / walkbook'), ('signs', 'Yard signs'),
        ('website', 'Website help'), ('fundraising', 'Fundraising help'),
        ('literature', 'Palm cards / literature'), ('mail', 'Mail'),
        ('training', 'Training'), ('video', 'Video / photos'),
        ('volunteers', 'Volunteers'), ('data', 'Data / targeting'),
        ('other', 'Something else')]

OUTCOME_KEYS = {k for k, _ in OUTCOMES}
STATUS_KEYS = {k for k, _ in STATUSES}
ASK_KEYS = {k for k, _ in ASKS}
ASK_LABEL = dict(ASKS)
STATUS_LABEL = dict(STATUSES)
OUTCOME_LABEL = dict(OUTCOMES)

# Sort weight: the races that decide the majority get called first.
RATING_RANK = {'SWING': 0, 'LEAN GOP': 1, 'LEAN DEM': 1,
               'LIKELY GOP': 2, 'LIKELY DEM': 2,
               'SAFE GOP': 3, 'SAFE DEM': 3}

# Corrections a whip can make from the call screen. Contact details and links
# go to the master candidate record; milestones to the progress tracker.
CAND_FIELDS = {'phone1', 'phone2', 'email', 'external_campaign_url',
               'donate_url', 'facebook_url', 'instagram_url', 'twitter_url'}
CCP_BOOL = {'fundraising_started', 'canvassing_started', 'signs_ordered',
            'training_attended', 'walkbook_done', 'consult_done'}
CCP_NUM = {'fundraising_amount', 'doors_knocked', 'signs_count'}
CCP_TEXT = {'training_name'}
FIX_FIELDS = CAND_FIELDS | CCP_BOOL | CCP_NUM | CCP_TEXT


# --------------------------------------------------------------- access
def _role():
    return (getattr(current_user, 'role', '') or '').lower() if current_user.is_authenticated else ''


def can_admin_whip():
    return bool(_can_admin and _can_admin())


def can_whip():
    return can_admin_whip() or _role() == 'whip'


def whip_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user.is_authenticated:
            flash("Please log in.", "warning")
            return redirect(url_for('login'))
        if not can_whip():
            flash("You don't have access to that.", "danger")
            return redirect(url_for('index'))
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not can_admin_whip():
            flash("Admins only.", "danger")
            return redirect(url_for('whip.my_week'))
        return f(*a, **k)
    return w


def _uid():
    """users.user_id — AdminUser.id is the flask-login key 'u_12', not an int."""
    uid = getattr(current_user, 'user_id', None)
    if uid is None:
        raw = str(getattr(current_user, 'id', '') or '')
        uid = raw[2:] if raw.startswith('u_') else raw
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


def _uname():
    return (getattr(current_user, 'username', None)
            or getattr(current_user, 'email', None) or 'unknown')


# ------------------------------------------------------------ the week
def _monday(d=None):
    d = d or date.today()
    return d - timedelta(days=d.weekday())


def _week_label(monday):
    end = monday + timedelta(days=6)
    if monday.month == end.month:
        return f"{monday:%b %-d}–{end:%-d}"
    return f"{monday:%b %-d} – {end:%b %-d}"


def _ago(when):
    """'3 days ago' / '2 weeks ago' — how stale our last contact is."""
    if not when:
        return None
    d = when.date() if isinstance(when, datetime) else when
    n = (date.today() - d).days
    if n <= 0:
        return 'today'
    if n == 1:
        return 'yesterday'
    if n < 14:
        return f'{n} days ago'
    if n < 60:
        return f'{n // 7} weeks ago'
    return f'{n // 30} months ago'


# ------------------------------------------------------------- plumbing
def _roster(cur, week):
    """Every filed candidate with tracking, assignment, race and check-in state.

    _build_rows() is campaign_progress's row builder — the same data the /progress
    matrix shows, so the whip screens can never drift from the tracker.
    """
    rows = _build_rows(cur)
    by_id = {d['candidate_id']: d for d in rows}

    cur.execute("""SELECT candidate_id, assigned_whip, last_contact_at, next_followup_at
                   FROM candidate_campaign_progress""")
    assign = {r[0]: {'owner': r[1], 'last_contact': r[2], 'followup': r[3]}
              for r in cur.fetchall()}

    cur.execute("SELECT user_id, COALESCE(username, email) FROM users")
    names = dict(cur.fetchall())

    # District competitiveness and seat count, from the recruitment DB's own
    # PVI table. Two seats in a swing district outrank one in a safe one.
    cur.execute("""SELECT full_district_code, max(pvi), max(pvi_rating), max(seat_count)
                   FROM districts WHERE full_district_code IS NOT NULL
                   GROUP BY full_district_code""")
    dist = {r[0]: {'pvi': float(r[1]) if r[1] is not None else None,
                   'rating': r[2] or '', 'seats': r[3]} for r in cur.fetchall()}

    # Contact details, kept off _build_rows because the matrix doesn't need them.
    cur.execute("""SELECT candidate_id, phone1, phone2, phone1_type, phone2_type,
                          email, email1, email2, city, signal_registered,
                          external_campaign_url, website_url, donate_url,
                          facebook_url, instagram_url, twitter_url
                   FROM candidates""")
    contact = {r[0]: {'phone1': r[1], 'phone2': r[2], 'phone1_type': r[3],
                      'phone2_type': r[4], 'email': r[5] or r[6] or r[7],
                      'town': r[8], 'signal': r[9],
                      'website_url': r[10] or r[11], 'donate_url': r[12],
                      'facebook_url': r[13], 'instagram_url': r[14],
                      'twitter_url': r[15]} for r in cur.fetchall()}

    # This week's check-in, plus the most recent one from any week.
    cur.execute("""SELECT candidate_id, week_start, contacted_by_name, contacted_at,
                          outcome, status, asks, ask_detail, notes
                   FROM whip_checkins WHERE week_start=%s""", (week,))
    this_week = {r[0]: _checkin(r) for r in cur.fetchall()}

    cur.execute("""SELECT DISTINCT ON (candidate_id) candidate_id, week_start,
                          contacted_by_name, contacted_at, outcome, status,
                          asks, ask_detail, notes
                   FROM whip_checkins ORDER BY candidate_id, week_start DESC""")
    latest = {r[0]: _checkin(r) for r in cur.fetchall()}

    for d in rows:
        a = assign.get(d['candidate_id'], {})
        rt = dist.get(d['district'], {})
        d['owner'] = a.get('owner')
        d['owner_name'] = names.get(a.get('owner'))
        d['followup'] = a.get('followup')
        d['pvi'] = rt.get('pvi')
        d['rating'] = rt.get('rating')
        d['seats'] = rt.get('seats') or 1
        d['rank'] = RATING_RANK.get(rt.get('rating'), 4)
        d['contact'] = contact.get(d['candidate_id'], {})
        d['checkin'] = this_week.get(d['candidate_id'])
        d['latest'] = latest.get(d['candidate_id'])
        d['last_contact'] = (d['latest'] or {}).get('at') or a.get('last_contact')
        d['ago'] = _ago(d['last_contact'])
        # Open asks carry forward until a later check-in stops listing them.
        d['open_asks'] = (d['latest'] or {}).get('asks') or []
        d['gaps'] = _gaps(d)
    return rows, by_id


def _checkin(r):
    return {'week': r[1], 'by': r[2], 'at': r[3], 'outcome': r[4],
            'status': r[5], 'asks': list(r[6]) if r[6] else [],
            'ask_detail': r[7], 'notes': r[8]}


# What to talk about, derived from the tracking we already hold. This is the
# column that makes the tool useful on day one: before anybody has logged a
# single call, the tracker already knows this candidate has no website, never
# collected a voter list and isn't raising money. Ordered by what actually
# costs a campaign the race, so the first three shown are the three worth
# raising on the phone.
GAP_CHECKS = [
    ('website',    lambda d: not d['website'],            'No website'),
    ('walkbook',   lambda d: not d['walkbook'],           'No voter list'),
    ('fundraising', lambda d: not d['fundraising'],       'Not fundraising'),
    ('canvassing', lambda d: not d['canvassing_started'], 'Not knocking'),
    ('portal',     lambda d: not d['portal'],             'No portal account'),
    ('login',      lambda d: d['portal'] and not d['last_login'], 'Never logged in'),
    ('signs',      lambda d: not d['signs_ordered'],      'No signs'),
    ('photo',      lambda d: not d['photo'],              'No photo'),
    ('donate',     lambda d: not d['donate'],             'No donate page'),
    ('socials',    lambda d: not d['socials'],            'No socials'),
    ('bio',        lambda d: not d['bio'],                'No bio'),
    ('training',   lambda d: not d['training_attended'],  'No training'),
]


def _gaps(d):
    return [label for _k, test, label in GAP_CHECKS if test(d)]


def _dkey(district):
    """Natural district sort: 'Cheshire 2' before 'Cheshire 10'."""
    d = (district or '~').strip()
    head, _, tail = d.rpartition(' ')
    if head and tail.isdigit():
        return (head.lower(), int(tail))
    return (d.lower(), 0)


def _lastname(d):
    return (d['name'] or '').split()[-1].lower() if d['name'] else ''


def _order(rows, sort='district'):
    """District order by default — that's how a board of 365 is read.

    'priority' is the other useful order: whoever hasn't been called, in the
    races that decide the majority, most seats first.
    """
    if sort == 'priority':
        return sorted(rows, key=lambda d: (
            0 if not d['checkin'] else 1,
            d['rank'],
            -(d['seats'] or 1),
            _lastname(d)))
    if sort == 'behind':
        return sorted(rows, key=lambda d: (d['score'], d['rank'], _lastname(d)))
    return sorted(rows, key=lambda d: (_dkey(d['district']), _lastname(d)))


def _whips(cur):
    """Assignable people: whips first, then admins who take a slice themselves."""
    cur.execute("""SELECT user_id, COALESCE(username, email), role FROM users
                   WHERE role IN ('whip','admin') ORDER BY (role='whip') DESC, 2""")
    return [{'user_id': r[0],
             'name': r[1] + ('' if r[2] == 'whip' else ' (admin)'),
             'role': r[2]} for r in cur.fetchall()]


# -------------------------------------------------------------- my week
@whip_bp.route('/whip')
@whip_required
def my_week():
    uid = _uid()
    week = _monday()
    # An admin runs the whole board, so that's what they open on. A whip opens
    # on their own list, which is the only thing they can act on.
    scope = request.args.get('scope') or ('all' if can_admin_whip() else 'mine')
    sort = request.args.get('sort', 'district')
    county = request.args.get('county', '')
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows, _ = _roster(cur, week)
        mine = [d for d in rows if d['owner'] == uid]
        counties = sorted({(d['district'] or '').rsplit(' ', 1)[0]
                           for d in rows if d['district']})

        # Scope is taken literally. An earlier version fell back to the whole
        # 365-candidate board when you had nothing assigned, which meant a
        # whip's page opened as a wall of rows that were nobody's job.
        if scope == 'all':
            shown = rows
        elif scope == 'unassigned':
            shown = [d for d in rows if not d['owner']]
        elif scope == 'needs':
            shown = [d for d in rows if (d['checkin'] or {}).get('asks') or d['open_asks']]
        else:
            shown = mine
        if county:
            shown = [d for d in shown if (d['district'] or '').startswith(county + ' ')]
        shown = _order(shown, sort)

        base = shown
        done = [d for d in base if d['checkin']]
        todo = [d for d in shown if not d['checkin']]

        # One line at the top of the page answering "what does my list need?"
        # without opening anybody.
        tally = {}
        for d in shown:
            for a in ((d['checkin'] or {}).get('asks') or d['open_asks']):
                tally[a] = tally.get(a, 0) + 1
        need_summary = [(k, ASK_LABEL.get(k, k), n)
                        for k, n in sorted(tally.items(), key=lambda x: -x[1])]

        return render_template(
            'whip/week.html',
            week=week, week_label=_week_label(week), rows=shown,
            todo=todo, done_rows=[d for d in shown if d['checkin']],
            scope=scope, mine_n=len(mine), total=len(rows),
            done=len(done), of=len(base), need_summary=need_summary,
            ask_label=ASK_LABEL, sort=sort, county=county, counties=counties,
            pct=int(round(100 * len(done) / len(base))) if base else 0)
    finally:
        cur.close()
        _release_db(conn)


# -------------------------------------------------------- know before you dial
@whip_bp.route('/whip/c/<int:cid>')
@whip_required
def candidate(cid):
    week = _monday()
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows, by_id = _roster(cur, week)
        d = by_id.get(cid)
        if not d:
            flash("That candidate isn't in the filed 2026 cohort.", "warning")
            return redirect(url_for('whip.my_week'))

        # Who else is on that ballot — teammates to coordinate with and the
        # opposition to size up, straight from the filings table.
        cur.execute("""SELECT first_name, last_name, party, candidate_id
                       FROM filings
                       WHERE election_year=2026 AND office='State Representative'
                         AND district_code=%s AND candidate_id IS DISTINCT FROM %s
                       ORDER BY party, last_name""", (d['district'], cid))
        ballot = [{'name': f"{r[0]} {r[1]}".strip(), 'party': r[2],
                   'candidate_id': r[3]} for r in cur.fetchall()]

        # Every prior week, most recent first.
        cur.execute("""SELECT candidate_id, week_start, contacted_by_name, contacted_at,
                              outcome, status, asks, ask_detail, notes
                       FROM whip_checkins
                       WHERE candidate_id=%s AND week_start < %s
                       ORDER BY week_start DESC LIMIT 12""", (cid, week))
        history = [_checkin(r) for r in cur.fetchall()]

        # SURVEY CONTENT IS NEVER SHOWN HERE. Survey ratings and notes (AFP,
        # CANH) are restricted to Chris and Osborne on /surveys. A whip sees
        # only whether a survey came back at all — the same completion-only
        # rule the candidate-facing /my-progress follows. Do not add
        # survey_org, rating or notes to this screen.

        # The recruitment team's internal assessment is admin-only too; a whip
        # doesn't need our private read on the person they're about to ring.
        admin_note = None
        if can_admin_whip():
            cur.execute("""SELECT assessment, notes, updated_by, updated_at
                           FROM candidate_admin_notes WHERE candidate_id=%s""", (cid,))
            n = cur.fetchone()
            admin_note = ({'assessment': n[0], 'notes': n[1], 'by': n[2], 'at': n[3]}
                          if n and (n[0] or n[1]) else None)

        cur.execute("""SELECT field, old_value, new_value, changed_by_name, changed_at
                       FROM whip_field_log WHERE candidate_id=%s
                       ORDER BY changed_at DESC LIMIT 8""", (cid,))
        fixes = [{'field': r[0], 'old': r[1], 'new': r[2], 'by': r[3], 'at': r[4]}
                 for r in cur.fetchall()]

        return render_template('whip/call.html', d=d, week=week,
                               week_label=_week_label(week), ballot=ballot,
                               history=history, admin_note=admin_note,
                               fixes=fixes,
                               outcomes=OUTCOMES, statuses=STATUSES, asks=ASKS,
                               ask_label=ASK_LABEL)
    finally:
        cur.close()
        _release_db(conn)


@whip_bp.route('/whip/c/<int:cid>/checkin', methods=['POST'])
@whip_required
def checkin(cid):
    f = request.form
    week = _monday()
    outcome = f.get('outcome') if f.get('outcome') in OUTCOME_KEYS else None
    status = f.get('status') if f.get('status') in STATUS_KEYS else None
    asks = [a for a in f.getlist('asks') if a in ASK_KEYS]
    detail = (f.get('ask_detail') or '').strip() or None
    notes = (f.get('notes') or '').strip() or None
    followup = (f.get('next_followup') or '').strip() or None

    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO whip_checkins (candidate_id, week_start, contacted_by,
                   contacted_by_name, outcome, status, asks, ask_detail, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (candidate_id, week_start) DO UPDATE SET
                contacted_by=EXCLUDED.contacted_by,
                contacted_by_name=EXCLUDED.contacted_by_name,
                contacted_at=now(), outcome=EXCLUDED.outcome,
                status=EXCLUDED.status, asks=EXCLUDED.asks,
                ask_detail=EXCLUDED.ask_detail, notes=EXCLUDED.notes
        """, (cid, week, _uid(), _uname(), outcome, status,
              asks or None, detail, notes))

        # Mirror onto the tracker so /progress and the assignment board show a
        # fresh contact date without reading the check-in log.
        cur.execute("""INSERT INTO candidate_campaign_progress
                            (candidate_id, last_contact_at, next_followup_at, needs,
                             updated_by, updated_at)
                       VALUES (%s, now(), %s, %s, %s, now())
                       ON CONFLICT (candidate_id) DO UPDATE SET
                            last_contact_at=now(),
                            next_followup_at=EXCLUDED.next_followup_at,
                            needs=EXCLUDED.needs,
                            updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (cid, followup, asks or None, _uname()))
        conn.commit()
        flash("Check-in saved.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Could not save: {str(e)[:120]}", "danger")
        return redirect(url_for('whip.candidate', cid=cid))
    finally:
        cur.close()
        _release_db(conn)

    if f.get('stay'):
        return redirect(url_for('whip.candidate', cid=cid))
    return redirect(url_for('whip.my_week'))


# ------------------------------------------------------------ corrections
@whip_bp.route('/whip/c/<int:cid>/fix', methods=['POST'])
@whip_required
def fix(cid):
    """Correct a tracked field from the call screen.

    Written through to the master record — a phone number a whip fixes here is
    the phone number the mail house gets — with the before/after in whip_field_log.
    """
    data = request.get_json(silent=True) or {}
    field = data.get('field')
    if field not in FIX_FIELDS:
        return jsonify({'error': 'unknown field'}), 400

    raw = data.get('value')
    if field in CCP_BOOL:
        val = bool(raw)
    elif field in CCP_NUM:
        s = str(raw or '').replace(',', '').replace('$', '').strip()
        if not s:
            val = None
        else:
            try:
                val = float(s) if field == 'fundraising_amount' else int(float(s))
            except ValueError:
                return jsonify({'error': 'not a number'}), 400
    else:
        val = (str(raw).strip() or None) if raw is not None else None

    table = 'candidates' if field in CAND_FIELDS else 'candidate_campaign_progress'
    conn = _get_db()
    cur = conn.cursor()
    try:
        if table == 'candidates':
            cur.execute(f"SELECT {field} FROM candidates WHERE candidate_id=%s", (cid,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'no such candidate'}), 404
            old = row[0]
            cur.execute(f"""UPDATE candidates SET {field}=%s, modified_by=%s,
                                   modified_at=now() WHERE candidate_id=%s""",
                        (val, _uname()[:50], cid))
        else:
            cur.execute(f"SELECT {field} FROM candidate_campaign_progress WHERE candidate_id=%s",
                        (cid,))
            row = cur.fetchone()
            old = row[0] if row else None
            cur.execute(f"""INSERT INTO candidate_campaign_progress
                                 (candidate_id, {field}, updated_by, updated_at)
                            VALUES (%s,%s,%s,now())
                            ON CONFLICT (candidate_id) DO UPDATE SET
                                 {field}=EXCLUDED.{field},
                                 updated_by=EXCLUDED.updated_by, updated_at=now()""",
                        (cid, val, _uname()))

        cur.execute("""INSERT INTO whip_field_log (candidate_id, field, old_value,
                              new_value, changed_by, changed_by_name)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (cid, field,
                     None if old is None else str(old),
                     None if val is None else str(val),
                     _uid(), _uname()))
        conn.commit()
        return jsonify({'success': True, 'value': val})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)[:120]}), 500
    finally:
        cur.close()
        _release_db(conn)


# ------------------------------------------------------------- the asks
@whip_bp.route('/whip/needs')
@admin_required
def needs():
    """What candidates asked for, grouped by what we'd have to deliver.

    This is the payoff for making the calls: a work order per ask type, with
    who asked, which whip heard it, and how long it has been sitting.
    """
    week = _monday()
    weeks_back = 4
    since = week - timedelta(weeks=weeks_back - 1)
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows, by_id = _roster(cur, week)
        cur.execute("""SELECT candidate_id, week_start, contacted_by_name, asks,
                              ask_detail, status
                       FROM whip_checkins
                       WHERE week_start >= %s AND asks IS NOT NULL
                       ORDER BY week_start DESC""", (since,))
        buckets = {k: [] for k, _ in ASKS}
        seen = set()
        for cid, wk, who, asks, detail, status in cur.fetchall():
            d = by_id.get(cid)
            if not d:
                continue
            for a in (asks or []):
                if a not in buckets or (cid, a) in seen:
                    continue    # only the most recent time they asked
                seen.add((cid, a))
                buckets[a].append({'d': d, 'week': wk, 'who': who,
                                   'detail': detail, 'status': status,
                                   'ago': _ago(wk)})

        cur.execute("""SELECT status, count(*) FROM whip_checkins
                       WHERE week_start=%s GROUP BY status""", (week,))
        status_counts = dict(cur.fetchall())

        return render_template(
            'whip/needs.html', week=week, week_label=_week_label(week),
            weeks_back=weeks_back,
            buckets=[(k, ASK_LABEL[k], buckets[k]) for k, _ in ASKS if buckets[k]],
            total=sum(len(v) for v in buckets.values()),
            status_counts=status_counts, status_label=STATUS_LABEL,
            flagged=_order([d for d in rows if (d['checkin'] or {}).get('status')
                            in ('at_risk', 'not_running')]),
            silent=_order([d for d in rows if d['owner'] and not d['last_contact']]))
    finally:
        cur.close()
        _release_db(conn)


# -------------------------------------------------------------- assign
@whip_bp.route('/whip/assign', methods=['GET', 'POST'])
@admin_required
def assign():
    week = _monday()
    conn = _get_db()
    cur = conn.cursor()
    try:
        if request.method == 'POST':
            w = request.form.get('assigned_whip')
            w = int(w) if (w or '').isdigit() else None
            ids = [int(i) for i in request.form.getlist('cand') if i.isdigit()]
            for cid in ids:
                cur.execute("""INSERT INTO candidate_campaign_progress
                                    (candidate_id, assigned_whip)
                               VALUES (%s,%s) ON CONFLICT (candidate_id)
                               DO UPDATE SET assigned_whip=EXCLUDED.assigned_whip""",
                            (cid, w))
            conn.commit()
            flash(f"{len(ids)} assigned." if ids else "Nothing was selected.",
                  "success" if ids else "warning")
            return redirect(url_for('whip.assign',
                                    county=request.form.get('county', ''),
                                    owner=request.form.get('owner', '')))

        rows, _ = _roster(cur, week)
        counties = sorted({(d['district'] or '').rsplit(' ', 1)[0]
                           for d in rows if d['district']})
        county = request.args.get('county', '')
        owner = request.args.get('owner', '')
        if county:
            rows = [d for d in rows if (d['district'] or '').startswith(county + ' ')]
        if owner == 'none':
            rows = [d for d in rows if not d['owner']]
        elif owner.isdigit():
            rows = [d for d in rows if d['owner'] == int(owner)]
        rows = sorted(rows, key=lambda d: (d['rank'], -(d['seats'] or 1),
                                           (d['district'] or ''), d['name'] or ''))
        return render_template('whip/assign.html', rows=rows, whips=_whips(cur),
                               counties=counties, county=county, owner=owner)
    finally:
        cur.close()
        _release_db(conn)


# -------------------------------------------------------------- roster
@whip_bp.route('/whip/whips')
@admin_required
def roster():
    week = _monday()
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT u.user_id, COALESCE(u.username,''), COALESCE(u.email,''),
                   u.role, u.last_login, COUNT(p.candidate_id)
            FROM users u
            LEFT JOIN candidate_campaign_progress p ON p.assigned_whip = u.user_id
            WHERE u.role='whip' OR p.candidate_id IS NOT NULL
            GROUP BY 1,2,3,4,5 ORDER BY 6 DESC, 2""")
        people = [{'user_id': r[0], 'username': r[1], 'email': r[2], 'role': r[3],
                   'last_login': r[4], 'assigned': r[5]} for r in cur.fetchall()]

        cur.execute("""SELECT contacted_by, count(*) FROM whip_checkins
                       WHERE week_start=%s GROUP BY 1""", (week,))
        done = dict(cur.fetchall())
        for p in people:
            p['done'] = done.get(p['user_id'], 0)
            p['pct'] = int(round(100 * p['done'] / p['assigned'])) if p['assigned'] else 0

        cur.execute("SELECT count(*) FROM candidate_campaign_progress WHERE assigned_whip IS NULL")
        return render_template('whip/roster.html', people=people, week=week,
                               week_label=_week_label(week))
    finally:
        cur.close()
        _release_db(conn)


@whip_bp.route('/whip/whips/add', methods=['POST'])
@admin_required
def roster_add():
    email = (request.form.get('email') or '').strip().lower()
    if '@' not in email:
        flash("A valid email is required.", "warning")
        return redirect(url_for('whip.roster'))
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM users WHERE LOWER(email)=%s", (email,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE users SET role='whip' WHERE user_id=%s", (row[0],))
            conn.commit()
            flash(f"{email} is now a whip.", "success")
            return redirect(url_for('whip.roster'))
        username = (request.form.get('username') or email.split('@')[0]).strip()
        cur.execute("""INSERT INTO users (username,email,password_hash,role,created_at)
                       VALUES (%s,%s,NULL,'whip',now()) RETURNING user_id""",
                    (username, email))
        uid = cur.fetchone()[0]
        conn.commit()
        try:
            from app import send_welcome_email
            send_welcome_email(email, username, 'admin', uid)
            flash(f"Whip added — setup email sent to {email}.", "success")
        except Exception:
            flash(f"Whip added as {username}, but the setup email failed. "
                  f"They can use Forgot password.", "warning")
    finally:
        cur.close()
        _release_db(conn)
    return redirect(url_for('whip.roster'))


@whip_bp.route('/whip/whips/<int:user_id>/remove', methods=['POST'])
@admin_required
def roster_remove(user_id):
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET role='admin' WHERE user_id=%s AND role='whip'",
                    (user_id,))
        conn.commit()
        flash("Whip role removed. Their assignments are untouched.", "success")
    finally:
        cur.close()
        _release_db(conn)
    return redirect(url_for('whip.roster'))


# -------------------------------------------------------------- export
@whip_bp.route('/whip/export.csv')
@admin_required
def export():
    week = _monday()
    conn = _get_db()
    cur = conn.cursor()
    try:
        rows, _ = _roster(cur, week)
        buf = io.StringIO()
        w = _csv.writer(buf)
        w.writerow(['candidate_id', 'name', 'district', 'seats', 'rating', 'pvi',
                    'whip', 'last_contact', 'outcome', 'status', 'asks',
                    'ask_detail', 'notes', 'logged_by', 'logged_at'])
        for d in _order(rows):
            c = d['checkin'] or {}
            w.writerow([d['candidate_id'], d['name'], d['district'], d['seats'],
                        d['rating'], d['pvi'], d['owner_name'] or '',
                        d['last_contact'] or '', c.get('outcome', ''),
                        c.get('status', ''), '; '.join(c.get('asks') or []),
                        c.get('ask_detail', ''), c.get('notes', ''),
                        c.get('by', ''), c.get('at', '')])
        return Response(buf.getvalue(), mimetype='text/csv', headers={
            'Content-Disposition': f'attachment; filename=whip_week_{week}.csv'})
    finally:
        cur.close()
        _release_db(conn)
