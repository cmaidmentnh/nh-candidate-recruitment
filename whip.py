"""
Whip tool — collect what's missing from 2026 R State Rep campaigns.

The point of a whip's call is not to record that a call happened. It is to come
off the phone having captured something: a website URL, a donate link, a dollar
figure, a photo. For most items the artifact IS the verification — you don't
tick "has a website", you paste the URL, and the moment it exists it is provable
and it flows into the directory, the digest and the palm cards.

Across the 365 filed candidates the gaps are large: 262 have no website on file,
307 no donate link, 276 no bio. That backlog is the job.

Screens
    /whip            dashboard - where the whole operation stands
    /whip/board      all candidates, filtered, sorted by what's missing
    /whip/c/<id>     the collection worksheet for one candidate
    /whip/whips      roster: add whips, see their load          (admin)
    /whip/assign     bulk assignment by county                  (admin)
    /whip/export.csv everything collected                       (admin)
"""
import io
import csv as _csv
from datetime import datetime, timedelta
from functools import wraps

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, Response)
from flask_login import current_user

whip_bp = Blueprint('whip', __name__)

_get_db = _release_db = _is_super_admin = None
_build_rows = _can_admin = None
_upload = None


def init_whip(get_db, release_db, build_rows, can_admin, upload_file=None):
    global _get_db, _release_db, _build_rows, _can_admin, _upload
    _get_db, _release_db = get_db, release_db
    _build_rows, _can_admin = build_rows, can_admin
    _upload = upload_file


# --------------------------------------------------------------- access
def _role():
    return (getattr(current_user, 'role', '') or '').lower() if current_user.is_authenticated else ''


def can_admin_whip():
    """Full control: roster, bulk assignment, export, the desktop matrix."""
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
            return redirect(url_for('whip.dashboard'))
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


# ------------------------------------------------------------ the items
# Each entry: key, label, hint, kind, and where it is stored.
# 'candidates' items are self-verifying — the value is the proof.
COLLECT = [
    ('website',     'Website',        'Their campaign site',            'url',
     'candidates', 'website_url'),
    ('donate',      'Donate link',    'WinRed, ActBlue or similar',     'url',
     'candidates', 'donate_url'),
    ('facebook',    'Facebook',       'Page or profile link',           'url',
     'candidates', 'facebook_url'),
    ('twitter',     'X / Twitter',    'Profile link',                   'url',
     'candidates', 'twitter_url'),
    ('instagram',   'Instagram',      'Profile link',                   'url',
     'candidates', 'instagram_url'),
    ('photo',       'Photo',          'Headshot for palm cards & web',  'photo',
     'candidates', 'photo_url'),
    ('bio',         'Bio',            'A short paragraph about them',   'text',
     'candidates', 'bio'),
    ('phone',       'Mobile number',  'So we can text them',            'tel',
     'candidates', 'phone1'),
    ('fundraising', 'Raised so far',  'Rough total is fine',            'money',
     'progress',   'fundraising_amount'),
    ('doors',       'Doors knocked',  'Their own estimate',             'number',
     'progress',   'doors_knocked'),
    ('signs',       'Yard signs',     'How many ordered',               'number',
     'progress',   'signs_count'),
    ('training',    'Training',       'Which one they attended',        'shorttext',
     'progress',   'training_name'),
]
CAND_COLS = {k: col for k, _l, _h, _t, tbl, col in COLLECT if tbl == 'candidates'}
PROG_COLS = {k: col for k, _l, _h, _t, tbl, col in COLLECT if tbl == 'progress'}

METHODS = ['call', 'text', 'email', 'in_person', 'voicemail']


# ------------------------------------------------------------- helpers
def _state(cur, rows):
    """Attach assignment, contact recency and the collected values."""
    cur.execute("""SELECT candidate_id, assigned_whip, last_contact_at,
                          fundraising_amount, doors_knocked, signs_count, training_name
                   FROM candidate_campaign_progress""")
    prog = {r[0]: r for r in cur.fetchall()}
    cur.execute("SELECT user_id, COALESCE(username, email) FROM users")
    names = dict(cur.fetchall())

    ids = [d['candidate_id'] for d in rows]
    vals = {}
    if ids:
        cols = ', '.join(sorted(set(CAND_COLS.values())))
        cur.execute(f"SELECT candidate_id, {cols} FROM candidates "
                    f"WHERE candidate_id = ANY(%s)", (ids,))
        order = sorted(set(CAND_COLS.values()))
        for r in cur.fetchall():
            vals[r[0]] = dict(zip(order, r[1:]))

    for d in rows:
        p = prog.get(d['candidate_id'])
        d['assigned_whip'] = p[1] if p else None
        d['assigned_name'] = names.get(p[1]) if p and p[1] else None
        d['last_contact_at'] = p[2] if p else None
        v = vals.get(d['candidate_id'], {})
        got = {}
        for key, col in CAND_COLS.items():
            got[key] = (v.get(col) or '').strip() if isinstance(v.get(col), str) else v.get(col)
        for i, key in enumerate(PROG_COLS):
            got[key] = p[3 + i] if p else None
        # a website may also live on the builder / external field
        d['collected'] = got
        d['have'] = [k for k, _l, _h, _t, _tb, _c in COLLECT if got.get(k)]
        d['missing'] = [k for k, _l, _h, _t, _tb, _c in COLLECT if not got.get(k)]
        d['n_have'] = len(d['have'])
        d['n_total'] = len(COLLECT)
        d['pct'] = int(round(100 * d['n_have'] / d['n_total']))
    return rows


def _priority(d, uid):
    """Mine first, then never-contacted, then the emptiest records."""
    return (0 if d['assigned_whip'] == uid else 1,
            0 if not d['last_contact_at'] else 1,
            d['n_have'],
            d['last_contact_at'] or datetime.min)


def _whips(cur):
    cur.execute("""SELECT user_id, COALESCE(username, email) FROM users
                   WHERE role IN ('whip','admin') ORDER BY 2""")
    return [{'user_id': r[0], 'name': r[1]} for r in cur.fetchall()]


def _rows(cur):
    return _state(cur, _build_rows(cur))


# ---------------------------------------------------------- dashboard
@whip_bp.route('/whip')
@whip_required
def dashboard():
    uid = _uid()
    conn = _get_db(); cur = conn.cursor()
    try:
        rows = _rows(cur)
        total = len(rows)
        gaps = {k: sum(1 for d in rows if not d['collected'].get(k))
                for k, _l, _h, _t, _tb, _c in COLLECT}
        labels = {k: l for k, l, _h, _t, _tb, _c in COLLECT}
        top_gaps = sorted(({'key': k, 'label': labels[k], 'n': n, 'pct': int(round(100*n/total)) if total else 0}
                           for k, n in gaps.items()), key=lambda x: -x['n'])

        cur.execute("""
            SELECT COALESCE(u.username,u.email), COUNT(p.candidate_id),
                   COUNT(p.last_contact_at)
            FROM candidate_campaign_progress p
            JOIN users u ON u.user_id = p.assigned_whip
            GROUP BY 1 ORDER BY 2 DESC""")
        per_whip = [{'who': r[0], 'assigned': r[1], 'contacted': r[2],
                     'pct': int(round(100*r[2]/r[1])) if r[1] else 0}
                    for r in cur.fetchall()]

        cur.execute("""
            SELECT k.contacted_at, k.contacted_by_name, k.method, k.reached,
                   c.first_name || ' ' || c.last_name
            FROM campaign_checkins k JOIN candidates c ON c.candidate_id=k.candidate_id
            ORDER BY k.contacted_at DESC LIMIT 12""")
        recent = [{'at': r[0], 'by': r[1], 'method': r[2], 'reached': r[3], 'name': r[4]}
                  for r in cur.fetchall()]

        collected = sum(d['n_have'] for d in rows)
        possible = total * len(COLLECT)
        mine = [d for d in rows if d['assigned_whip'] == uid]
        return render_template('whip/dashboard.html',
                               total=total, contacted=sum(1 for d in rows if d['last_contact_at']),
                               unassigned=sum(1 for d in rows if not d['assigned_whip']),
                               collected=collected, possible=possible,
                               pct=int(round(100*collected/possible)) if possible else 0,
                               top_gaps=top_gaps[:6], per_whip=per_whip, recent=recent,
                               mine_n=len(mine))
    finally:
        cur.close(); _release_db(conn)


# -------------------------------------------------------------- board
@whip_bp.route('/whip/board')
@whip_required
def board():
    uid = _uid()
    view = request.args.get('view', 'mine' if _role() == 'whip' else 'all')
    q = (request.args.get('q') or '').strip().lower()
    conn = _get_db(); cur = conn.cursor()
    try:
        rows = _rows(cur)
        rows.sort(key=lambda d: _priority(d, uid))
        mine_n = sum(1 for d in rows if d['assigned_whip'] == uid)
        if view == 'mine':
            rows = [d for d in rows if d['assigned_whip'] == uid]
        elif view == 'never':
            rows = [d for d in rows if not d['last_contact_at']]
        elif view == 'empty':
            rows = [d for d in rows if d['n_have'] <= 3]
        if q:
            rows = [d for d in rows
                    if q in (d['name'] or '').lower() or q in (d['district'] or '').lower()]
        return render_template('whip/board.html', rows=rows, view=view, q=q,
                               mine_n=mine_n, labels={k: l for k, l, *_ in COLLECT})
    finally:
        cur.close(); _release_db(conn)


# ---------------------------------------------------------- candidate
@whip_bp.route('/whip/c/<int:cid>')
@whip_required
def candidate(cid):
    conn = _get_db(); cur = conn.cursor()
    try:
        rows = _rows(cur)
        d = next((r for r in rows if r['candidate_id'] == cid), None)
        if not d:
            flash("Not in the 2026 filed cohort.", "warning")
            return redirect(url_for('whip.board'))

        cur.execute("""SELECT id, contacted_at, contacted_by_name, method, reached,
                              notes, contacted_by
                       FROM campaign_checkins WHERE candidate_id=%s
                       ORDER BY contacted_at DESC LIMIT 12""", (cid,))
        me = _uid()
        log = [{'id': r[0], 'at': r[1], 'by': r[2], 'method': r[3], 'reached': r[4],
                'notes': r[5], 'mine': (r[6] == me) or can_admin_whip()}
               for r in cur.fetchall()]

        cur.execute("SELECT phone1, phone2, email, email1 FROM candidates WHERE candidate_id=%s",
                    (cid,))
        c = cur.fetchone() or (None,)*4
        items = [{'key': k, 'label': l, 'hint': h, 'kind': t,
                  'value': d['collected'].get(k)} for k, l, h, t, _tb, _c in COLLECT]
        return render_template('whip/candidate.html', d=d,
                               need=[i for i in items if not i['value']],
                               have=[i for i in items if i['value']],
                               log=log, methods=METHODS, whips=_whips(cur),
                               contact={'phone': c[0] or c[1], 'email': c[2] or c[3]})
    finally:
        cur.close(); _release_db(conn)


@whip_bp.route('/whip/c/<int:cid>/collect', methods=['POST'])
@whip_required
def collect(cid):
    """Save one collected field. One field at a time so nothing is ever lost."""
    key = request.form.get('key')
    raw = (request.form.get('value') or '').strip()
    conn = _get_db(); cur = conn.cursor()
    try:
        if key == 'photo' and 'file' in request.files and request.files['file'].filename:
            f = request.files['file']
            if not _upload:
                return jsonify({'ok': False, 'error': 'uploads not configured'}), 500
            safe = f"candidate-photos/{cid}-{int(datetime.utcnow().timestamp())}-{f.filename}"
            raw = _upload(f, safe) or ''
            if not raw:
                return jsonify({'ok': False, 'error': 'upload failed'}), 500

        if key in CAND_COLS:
            cur.execute(f"UPDATE candidates SET {CAND_COLS[key]}=%s WHERE candidate_id=%s",
                        (raw or None, cid))
        elif key in PROG_COLS:
            col = PROG_COLS[key]
            val = raw.replace('$', '').replace(',', '').strip() or None
            cur.execute(f"""INSERT INTO candidate_campaign_progress (candidate_id, {col}, updated_by, updated_at)
                            VALUES (%s,%s,%s,now())
                            ON CONFLICT (candidate_id) DO UPDATE
                            SET {col}=EXCLUDED.{col}, updated_by=EXCLUDED.updated_by,
                                updated_at=now()""", (cid, val, _uname()))
        else:
            return jsonify({'ok': False, 'error': 'unknown field'}), 400
        conn.commit()
        return jsonify({'ok': True, 'value': raw})
    except Exception as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        cur.close(); _release_db(conn)


@whip_bp.route('/whip/c/<int:cid>/log', methods=['POST'])
@whip_required
def log_call(cid):
    f = request.form
    conn = _get_db(); cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO campaign_checkins
                       (candidate_id, contacted_by, contacted_by_name, method, reached, notes)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (cid, _uid(), _uname(),
                     f.get('method') if f.get('method') in METHODS else None,
                     f.get('reached') == 'yes',
                     (f.get('notes') or '').strip() or None))
        cur.execute("""INSERT INTO candidate_campaign_progress (candidate_id, last_contact_at, updated_by, updated_at)
                       VALUES (%s, now(), %s, now())
                       ON CONFLICT (candidate_id) DO UPDATE
                       SET last_contact_at=now(), updated_by=EXCLUDED.updated_by, updated_at=now()""",
                    (cid, _uname()))
        conn.commit()
        flash("Call logged.", "success")
    finally:
        cur.close(); _release_db(conn)
    return redirect(url_for('whip.candidate', cid=cid))


@whip_bp.route('/whip/log/<int:log_id>/delete', methods=['POST'])
@whip_required
def log_delete(log_id):
    conn = _get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT candidate_id, contacted_by FROM campaign_checkins WHERE id=%s",
                    (log_id,))
        row = cur.fetchone()
        if not row:
            return redirect(url_for('whip.board'))
        cid, by = row
        if not (can_admin_whip() or by == _uid()):
            flash("That isn't yours to delete.", "danger")
            return redirect(url_for('whip.candidate', cid=cid))
        cur.execute("DELETE FROM campaign_checkins WHERE id=%s", (log_id,))
        cur.execute("""UPDATE candidate_campaign_progress SET last_contact_at =
                       (SELECT MAX(contacted_at) FROM campaign_checkins WHERE candidate_id=%s)
                       WHERE candidate_id=%s""", (cid, cid))
        conn.commit()
        flash("Call deleted.", "success")
        return redirect(url_for('whip.candidate', cid=cid))
    finally:
        cur.close(); _release_db(conn)


# ------------------------------------------------------------- roster
@whip_bp.route('/whip/whips')
@admin_required
def roster():
    conn = _get_db(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT u.user_id, COALESCE(u.username,''), COALESCE(u.email,''), u.role,
                   u.last_login, COUNT(p.candidate_id), COUNT(p.last_contact_at)
            FROM users u
            LEFT JOIN candidate_campaign_progress p ON p.assigned_whip=u.user_id
            WHERE u.role IN ('whip','admin')
            GROUP BY 1,2,3,4,5
            ORDER BY (u.role='whip') DESC, 6 DESC, 2""")
        people = [{'user_id': r[0], 'username': r[1], 'email': r[2], 'role': r[3],
                   'last_login': r[4], 'assigned': r[5], 'contacted': r[6],
                   'pct': int(round(100*r[6]/r[5])) if r[5] else 0}
                  for r in cur.fetchall()]
        return render_template('whip/roster.html', people=people)
    finally:
        cur.close(); _release_db(conn)


@whip_bp.route('/whip/whips/add', methods=['POST'])
@admin_required
def roster_add():
    email = (request.form.get('email') or '').strip().lower()
    if '@' not in email:
        flash("A valid email is required.", "warning")
        return redirect(url_for('whip.roster'))
    conn = _get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, role FROM users WHERE LOWER(email)=%s", (email,))
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
            flash(f"Whip added as {username}. Setup email failed; "
                  f"they can use Forgot password.", "warning")
    finally:
        cur.close(); _release_db(conn)
    return redirect(url_for('whip.roster'))


@whip_bp.route('/whip/whips/<int:user_id>/remove', methods=['POST'])
@admin_required
def roster_remove(user_id):
    conn = _get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET role='admin' WHERE user_id=%s AND role='whip'",
                    (user_id,))
        conn.commit()
        flash("Whip role removed.", "success")
    finally:
        cur.close(); _release_db(conn)
    return redirect(url_for('whip.roster'))


# -------------------------------------------------------- bulk assign
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
                               VALUES (%s,%s)
                               ON CONFLICT (candidate_id) DO UPDATE
                               SET assigned_whip=EXCLUDED.assigned_whip""", (cid, w))
            conn.commit()
            flash(f"{len(ids)} assigned." if ids else "Nothing selected.",
                  "success" if ids else "warning")
            return redirect(url_for('whip.assign', county=request.form.get('county', ''),
                                    owner=request.form.get('owner', '')))

        rows = _rows(cur)
        county = request.args.get('county', '')
        owner = request.args.get('owner', '')
        counties = sorted({(d['district'] or '').rsplit(' ', 1)[0] for d in rows if d['district']})
        if county:
            rows = [d for d in rows if (d['district'] or '').startswith(county + ' ')]
        if owner == 'none':
            rows = [d for d in rows if not d['assigned_whip']]
        elif owner.isdigit():
            rows = [d for d in rows if d['assigned_whip'] == int(owner)]
        return render_template('whip/assign.html', rows=rows, whips=_whips(cur),
                               counties=counties, county=county, owner=owner)
    finally:
        cur.close(); _release_db(conn)


# ------------------------------------------------------------- export
@whip_bp.route('/whip/export.csv')
@admin_required
def export():
    conn = _get_db(); cur = conn.cursor()
    try:
        rows = _rows(cur)
        buf = io.StringIO(); w = _csv.writer(buf)
        keys = [k for k, *_ in COLLECT]
        w.writerow(['candidate_id', 'name', 'district', 'assigned_whip',
                    'last_contact', 'collected', 'of'] + keys)
        for d in rows:
            w.writerow([d['candidate_id'], d['name'], d['district'],
                        d['assigned_name'] or '', d['last_contact_at'] or '',
                        d['n_have'], d['n_total']]
                       + [d['collected'].get(k) or '' for k in keys])
        return Response(buf.getvalue(), mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename=whip_collection.csv'})
    finally:
        cur.close(); _release_db(conn)
