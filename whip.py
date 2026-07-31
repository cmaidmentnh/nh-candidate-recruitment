"""
Candidate whip tool — rounds of check-in calls.

Built on the one thing both working whip tools share: work arrives as a ROUND.
whip.nhhouse.gop runs survey rounds and lands 173-185 of 223 members every
time, because the job is finite and finishable — "get my 20 answered before
session". Nothing goes out to the member; the whip rings them and types the
answers in.

Applied here: an admin opens a round, every whip has an assigned slice of the
365 filed candidates, and the list is sorted by DISTRICT COMPETITIVENESS so the
races that decide the majority get called first — a Tossup with 8 seats before
a Safe R incumbent in Coos.

    /whip            my list for the open round + completion
    /whip/c/<id>     the seven questions
    /whip/results    what everyone said                      (admin)
    /whip/rounds     open and close rounds                   (admin)
    /whip/assign     bulk assignment                         (admin)
    /whip/whips      roster                                  (admin)
"""
import io
import csv as _csv
from functools import wraps

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response)
from flask_login import current_user

whip_bp = Blueprint('whip', __name__)
_get_db = _release_db = _build_rows = _can_admin = None


def init_whip(get_db, release_db, build_rows, can_admin, upload_file=None):
    global _get_db, _release_db, _build_rows, _can_admin
    _get_db, _release_db, _build_rows, _can_admin = (get_db, release_db,
                                                     build_rows, can_admin)


# ---------------------------------------------------------------- the ask
# Seven questions, short enough to get through in about four minutes.
# CAMPAIGNING and CONCERN are the two only a human can produce, and the two
# leadership actually wants. Everything else is byproduct.
QUESTIONS = [
    ('campaigning', 'Are they actually campaigning?',
     [('yes', 'Yes'), ('barely', 'Barely'), ('no', 'No'), ('no_contact', "Couldn't reach")]),
    ('canvassing', 'Door-knocking started?',
     [('yes', 'Yes'), ('not_yet', 'Not yet')]),
    ('signs', 'Signs out?',
     [('yes', 'Up'), ('ordered', 'Ordered'), ('no', 'No')]),
    ('concern', 'Do we need to worry about this race?',
     [('fine', 'Fine'), ('watch', 'Watch'), ('problem', 'Problem')]),
]
NEEDS = [('walkbook', 'Walkbook'), ('signs', 'Yard signs'), ('video', 'Video'),
         ('training', 'Training'), ('mail', 'Mail'), ('money', 'Money help'),
         ('nothing', 'Nothing')]
VALID = {k: {v for v, _ in opts} for k, _q, opts in QUESTIONS}
NEED_KEYS = {k for k, _ in NEEDS}

# Sort weight: the races that decide the majority get called first.
RATING_RANK = {'SWING': 0, 'LEAN GOP': 1, 'LEAN DEM': 1,
               'LIKELY GOP': 2, 'LIKELY DEM': 2,
               'SAFE GOP': 3, 'SAFE DEM': 3}


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
            flash("Please log in.", "warning"); return redirect(url_for('login'))
        if not can_whip():
            flash("You don't have access to that.", "danger"); return redirect(url_for('index'))
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not can_admin_whip():
            flash("Admins only.", "danger"); return redirect(url_for('whip.my_list'))
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


# -------------------------------------------------------------- plumbing
def _open_round(cur):
    cur.execute("SELECT id, title FROM whip_rounds WHERE is_open ORDER BY id DESC LIMIT 1")
    r = cur.fetchone()
    return {'id': r[0], 'title': r[1]} if r else None


def _roster(cur, round_id):
    """Every filed candidate with their district rating, owner and answer."""
    rows = _build_rows(cur)

    cur.execute("""SELECT candidate_id, assigned_whip FROM candidate_campaign_progress""")
    owner = dict(cur.fetchall())
    cur.execute("SELECT user_id, COALESCE(username, email) FROM users")
    names = dict(cur.fetchall())

    # district competitiveness, refreshed in the recruitment DB this cycle
    cur.execute("""SELECT DISTINCT full_district_code, pvi, pvi_rating
                   FROM districts WHERE full_district_code IS NOT NULL""")
    rating = {r[0]: {'pvi': float(r[1]) if r[1] is not None else None,
                     'rating': r[2] or ''} for r in cur.fetchall()}

    ans = {}
    if round_id:
        cur.execute("""SELECT candidate_id, campaigning, canvassing, signs, concern,
                              raised, needs, notes, answered_name, answered_at
                       FROM whip_answers WHERE round_id=%s""", (round_id,))
        for r in cur.fetchall():
            ans[r[0]] = {'campaigning': r[1], 'canvassing': r[2], 'signs': r[3],
                         'concern': r[4], 'raised': r[5],
                         'needs': list(r[6]) if r[6] else [], 'notes': r[7],
                         'by': r[8], 'at': r[9]}

    for d in rows:
        rt = rating.get(d['district'], {})
        d['pvi'] = rt.get('pvi')
        d['rating'] = rt.get('rating')
        d['rank'] = RATING_RANK.get(rt.get('rating'), 4)
        d['owner'] = owner.get(d['candidate_id'])
        d['owner_name'] = names.get(owner.get(d['candidate_id']))
        d['answer'] = ans.get(d['candidate_id'])
    return rows


def _sorted(rows, uid):
    """Mine first, unanswered first, then the closest seats, then most seats."""
    return sorted(rows, key=lambda d: (
        0 if d['owner'] == uid else 1,
        0 if not d['answer'] else 1,
        d['rank'],
        -(d.get('seats') or 1),
        d['name'] or ''))


def _whips(cur):
    """Assignable people: whips first, then admins who can take a slice."""
    cur.execute("""SELECT user_id, COALESCE(username, email), role FROM users
                   WHERE role IN ('whip','admin')
                   ORDER BY (role='whip') DESC, 2""")
    return [{'user_id': r[0],
             'name': r[1] + ('' if r[2] == 'whip' else ' (admin)'),
             'role': r[2]} for r in cur.fetchall()]


# ------------------------------------------------------------- my list
@whip_bp.route('/whip')
@whip_required
def my_list():
    uid = _uid()
    scope = request.args.get('scope', 'mine')
    conn = _get_db(); cur = conn.cursor()
    try:
        rnd = _open_round(cur)
        rows = _sorted(_roster(cur, rnd['id'] if rnd else None), uid)
        mine = [d for d in rows if d['owner'] == uid]
        shown = mine if (scope == 'mine' and mine) else rows
        if scope == 'todo':
            shown = [d for d in shown if not d['answer']]
        base = mine if mine else rows
        done = sum(1 for d in base if d['answer'])
        return render_template('whip/list.html', rnd=rnd, rows=shown, scope=scope,
                               mine_n=len(mine), total=len(rows),
                               done=done, of=len(base),
                               pct=int(round(100*done/len(base))) if base else 0)
    finally:
        cur.close(); _release_db(conn)


# ------------------------------------------------------------ the call
@whip_bp.route('/whip/c/<int:cid>')
@whip_required
def candidate(cid):
    conn = _get_db(); cur = conn.cursor()
    try:
        rnd = _open_round(cur)
        rows = _roster(cur, rnd['id'] if rnd else None)
        d = next((r for r in rows if r['candidate_id'] == cid), None)
        if not d:
            flash("Not in the filed cohort.", "warning")
            return redirect(url_for('whip.my_list'))
        cur.execute("""SELECT r.title, a.answered_name, a.answered_at, a.campaigning,
                              a.concern, a.notes
                       FROM whip_answers a JOIN whip_rounds r ON r.id=a.round_id
                       WHERE a.candidate_id=%s AND a.round_id <> COALESCE(%s,0)
                       ORDER BY a.answered_at DESC LIMIT 6""",
                    (cid, rnd['id'] if rnd else None))
        history = [{'round': r[0], 'by': r[1], 'at': r[2], 'campaigning': r[3],
                    'concern': r[4], 'notes': r[5]} for r in cur.fetchall()]
        cur.execute("SELECT phone1, phone2, email, email1 FROM candidates WHERE candidate_id=%s",
                    (cid,))
        c = cur.fetchone() or (None,)*4
        return render_template('whip/call.html', d=d, rnd=rnd, questions=QUESTIONS,
                               needs=NEEDS, history=history,
                               contact={'phone': c[0] or c[1], 'email': c[2] or c[3]})
    finally:
        cur.close(); _release_db(conn)


@whip_bp.route('/whip/c/<int:cid>/answer', methods=['POST'])
@whip_required
def answer(cid):
    f = request.form
    conn = _get_db(); cur = conn.cursor()
    try:
        rnd = _open_round(cur)
        if not rnd:
            flash("No round is open.", "warning")
            return redirect(url_for('whip.my_list'))
        vals = {k: (f.get(k) if f.get(k) in VALID[k] else None) for k, _q, _o in QUESTIONS}
        raised = (f.get('raised') or '').replace('$', '').replace(',', '').strip() or None
        needs = [n for n in f.getlist('needs') if n in NEED_KEYS]
        cur.execute("""
            INSERT INTO whip_answers (round_id, candidate_id, answered_by, answered_name,
                   reached, campaigning, canvassing, signs, concern, raised, needs, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (round_id, candidate_id) DO UPDATE SET
                answered_by=EXCLUDED.answered_by, answered_name=EXCLUDED.answered_name,
                answered_at=now(), reached=EXCLUDED.reached,
                campaigning=EXCLUDED.campaigning, canvassing=EXCLUDED.canvassing,
                signs=EXCLUDED.signs, concern=EXCLUDED.concern, raised=EXCLUDED.raised,
                needs=EXCLUDED.needs, notes=EXCLUDED.notes
        """, (rnd['id'], cid, _uid(), _uname(),
              vals['campaigning'] != 'no_contact', vals['campaigning'],
              vals['canvassing'], vals['signs'], vals['concern'], raised,
              needs or None, (f.get('notes') or '').strip() or None))
        cur.execute("""INSERT INTO candidate_campaign_progress (candidate_id, last_contact_at, updated_by, updated_at)
                       VALUES (%s, now(), %s, now())
                       ON CONFLICT (candidate_id) DO UPDATE
                       SET last_contact_at=now(), updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (cid, _uname()))
        conn.commit()
        flash("Saved.", "success")
    finally:
        cur.close(); _release_db(conn)
    return redirect(url_for('whip.my_list'))


# ------------------------------------------------------------- results
@whip_bp.route('/whip/results')
@admin_required
def results():
    conn = _get_db(); cur = conn.cursor()
    try:
        rnd = _open_round(cur)
        rows = _roster(cur, rnd['id'] if rnd else None)
        answered = [d for d in rows if d['answer']]
        by = lambda k, v: sum(1 for d in answered if d['answer'].get(k) == v)
        needs_count = {}
        for d in answered:
            for n in d['answer'].get('needs') or []:
                needs_count[n] = needs_count.get(n, 0) + 1
        cur.execute("""SELECT COALESCE(u.username,u.email), COUNT(a.id)
                       FROM whip_answers a LEFT JOIN users u ON u.user_id=a.answered_by
                       WHERE a.round_id=%s GROUP BY 1 ORDER BY 2 DESC""",
                    (rnd['id'] if rnd else 0,))
        per_whip = [{'who': r[0] or 'unknown', 'n': r[1]} for r in cur.fetchall()]
        return render_template('whip/results.html', rnd=rnd, rows=rows,
                               answered=answered, per_whip=per_whip,
                               problems=[d for d in answered if d['answer'].get('concern') == 'problem'],
                               watch=[d for d in answered if d['answer'].get('concern') == 'watch'],
                               not_running=[d for d in answered if d['answer'].get('campaigning') in ('no', 'barely')],
                               needs_count=sorted(needs_count.items(), key=lambda x: -x[1]),
                               stats={'total': len(rows), 'answered': len(answered),
                                      'fine': by('concern', 'fine'),
                                      'pct': int(round(100*len(answered)/len(rows))) if rows else 0})
    finally:
        cur.close(); _release_db(conn)


# -------------------------------------------------------------- rounds
@whip_bp.route('/whip/rounds', methods=['GET', 'POST'])
@admin_required
def rounds():
    conn = _get_db(); cur = conn.cursor()
    try:
        if request.method == 'POST':
            act = request.form.get('action')
            if act == 'open':
                title = (request.form.get('title') or '').strip()
                if title:
                    cur.execute("UPDATE whip_rounds SET is_open=false, closed_at=now() WHERE is_open")
                    cur.execute("INSERT INTO whip_rounds (title) VALUES (%s)", (title,))
                    conn.commit(); flash(f"Round '{title}' opened.", "success")
            elif act == 'close':
                cur.execute("UPDATE whip_rounds SET is_open=false, closed_at=now() WHERE is_open")
                conn.commit(); flash("Round closed.", "success")
            return redirect(url_for('whip.rounds'))
        cur.execute("""SELECT r.id, r.title, r.is_open, r.opened_at, r.closed_at,
                              COUNT(a.id)
                       FROM whip_rounds r LEFT JOIN whip_answers a ON a.round_id=r.id
                       GROUP BY 1,2,3,4,5 ORDER BY r.id DESC""")
        rs = [{'id': r[0], 'title': r[1], 'is_open': r[2], 'opened_at': r[3],
               'closed_at': r[4], 'n': r[5]} for r in cur.fetchall()]
        return render_template('whip/rounds.html', rounds=rs)
    finally:
        cur.close(); _release_db(conn)


# -------------------------------------------------------------- roster
@whip_bp.route('/whip/whips')
@admin_required
def roster():
    conn = _get_db(); cur = conn.cursor()
    try:
        # Whips only. Listing every admin here meant the roster showed all 47
        # accounts when the actual whip count was zero.
        cur.execute("""
            SELECT u.user_id, COALESCE(u.username,''), COALESCE(u.email,''), u.role,
                   u.last_login, COUNT(p.candidate_id)
            FROM users u
            LEFT JOIN candidate_campaign_progress p ON p.assigned_whip=u.user_id
            WHERE u.role = 'whip'
            GROUP BY 1,2,3,4,5 ORDER BY 6 DESC, 2""")
        people = [{'user_id': r[0], 'username': r[1], 'email': r[2], 'role': r[3],
                   'last_login': r[4], 'assigned': r[5]} for r in cur.fetchall()]
        rnd = _open_round(cur)
        if rnd:
            cur.execute("""SELECT answered_by, COUNT(*) FROM whip_answers
                           WHERE round_id=%s GROUP BY 1""", (rnd['id'],))
            done = dict(cur.fetchall())
            for p in people:
                p['done'] = done.get(p['user_id'], 0)
                p['pct'] = int(round(100*p['done']/p['assigned'])) if p['assigned'] else 0
        return render_template('whip/roster.html', people=people, rnd=rnd)
    finally:
        cur.close(); _release_db(conn)


@whip_bp.route('/whip/whips/add', methods=['POST'])
@admin_required
def roster_add():
    email = (request.form.get('email') or '').strip().lower()
    if '@' not in email:
        flash("A valid email is required.", "warning"); return redirect(url_for('whip.roster'))
    conn = _get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM users WHERE LOWER(email)=%s", (email,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE users SET role='whip' WHERE user_id=%s", (row[0],))
            conn.commit(); flash(f"{email} is now a whip.", "success")
            return redirect(url_for('whip.roster'))
        username = (request.form.get('username') or email.split('@')[0]).strip()
        cur.execute("""INSERT INTO users (username,email,password_hash,role,created_at)
                       VALUES (%s,%s,NULL,'whip',now()) RETURNING user_id""", (username, email))
        uid = cur.fetchone()[0]; conn.commit()
        try:
            from app import send_welcome_email
            send_welcome_email(email, username, 'admin', uid)
            flash(f"Whip added — setup email sent to {email}.", "success")
        except Exception:
            flash(f"Whip added as {username}; setup email failed. "
                  f"They can use Forgot password.", "warning")
    finally:
        cur.close(); _release_db(conn)
    return redirect(url_for('whip.roster'))


@whip_bp.route('/whip/whips/<int:user_id>/remove', methods=['POST'])
@admin_required
def roster_remove(user_id):
    conn = _get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET role='admin' WHERE user_id=%s AND role='whip'", (user_id,))
        conn.commit(); flash("Whip role removed.", "success")
    finally:
        cur.close(); _release_db(conn)
    return redirect(url_for('whip.roster'))


# -------------------------------------------------------------- assign
@whip_bp.route('/whip/assign', methods=['GET', 'POST'])
@admin_required
def assign():
    conn = _get_db(); cur = conn.cursor()
    try:
        if request.method == 'POST':
            w = request.form.get('assigned_whip')
            w = int(w) if (w or '').isdigit() else None
            ids = [int(i) for i in request.form.getlist('cand') if i.isdigit()]
            for cid in ids:
                cur.execute("""INSERT INTO candidate_campaign_progress (candidate_id, assigned_whip)
                               VALUES (%s,%s) ON CONFLICT (candidate_id)
                               DO UPDATE SET assigned_whip=EXCLUDED.assigned_whip""", (cid, w))
            conn.commit()
            flash(f"{len(ids)} assigned." if ids else "Nothing selected.",
                  "success" if ids else "warning")
            return redirect(url_for('whip.assign', county=request.form.get('county', ''),
                                    owner=request.form.get('owner', '')))
        rnd = _open_round(cur)
        rows = _roster(cur, rnd['id'] if rnd else None)
        counties = sorted({(d['district'] or '').rsplit(' ', 1)[0] for d in rows if d['district']})
        county = request.args.get('county', ''); owner = request.args.get('owner', '')
        if county:
            rows = [d for d in rows if (d['district'] or '').startswith(county + ' ')]
        if owner == 'none':
            rows = [d for d in rows if not d['owner']]
        elif owner.isdigit():
            rows = [d for d in rows if d['owner'] == int(owner)]
        rows = sorted(rows, key=lambda d: (d['rank'], -(d.get('seats') or 1), d['name'] or ''))
        return render_template('whip/assign.html', rows=rows, whips=_whips(cur),
                               counties=counties, county=county, owner=owner)
    finally:
        cur.close(); _release_db(conn)


# -------------------------------------------------------------- export
@whip_bp.route('/whip/export.csv')
@admin_required
def export():
    conn = _get_db(); cur = conn.cursor()
    try:
        rnd = _open_round(cur)
        rows = _roster(cur, rnd['id'] if rnd else None)
        buf = io.StringIO(); w = _csv.writer(buf)
        w.writerow(['candidate_id', 'name', 'district', 'seats', 'rating', 'pvi',
                    'whip', 'campaigning', 'canvassing', 'signs', 'concern',
                    'raised', 'needs', 'notes', 'answered_by', 'answered_at'])
        for d in rows:
            a = d['answer'] or {}
            w.writerow([d['candidate_id'], d['name'], d['district'], d.get('seats'),
                        d['rating'], d['pvi'], d['owner_name'] or '',
                        a.get('campaigning', ''), a.get('canvassing', ''),
                        a.get('signs', ''), a.get('concern', ''), a.get('raised', ''),
                        '; '.join(a.get('needs') or []), a.get('notes', ''),
                        a.get('by', ''), a.get('at', '')])
        return Response(buf.getvalue(), mimetype='text/csv', headers={
            'Content-Disposition': f'attachment; filename=whip_{(rnd or {}).get("title","round")}.csv'})
    finally:
        cur.close(); _release_db(conn)
