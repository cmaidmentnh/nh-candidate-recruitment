"""Shareable, view-only battlefield page.

Chris's rule for this page: **no money on it, ever.** It shows which districts CTEHR is
working, how hard, who the candidates are and what channels they get. It does not show
dollars, piece counts, universe sizes or anything else that tells an opponent the size of the
cheque. Anyone with the password can read it, so assume the NHDP eventually will.

Gated by a single shared password (PLAN_PASSWORD), the same pattern as the yard-sign site.
"""
import os
import hmac
from flask import Blueprint, render_template, request, session, redirect, url_for

public_plan_bp = Blueprint('publicplan', __name__)

get_db_connection = None
release_db_connection = None

SESSION_KEY = 'plan_ok'


def init_public_plan(db_conn_func, db_release_func):
    global get_db_connection, release_db_connection
    get_db_connection = db_conn_func
    release_db_connection = db_release_func


def _password():
    return os.environ.get('PLAN_PASSWORD', '')


@public_plan_bp.route('/plan', methods=['GET', 'POST'])
def public_plan():
    pw = _password()
    if request.method == 'POST':
        given = (request.form.get('password') or '').strip()
        # constant-time compare so the gate cannot be probed by timing
        if pw and hmac.compare_digest(given, pw):
            session[SESSION_KEY] = True
            return redirect(url_for('publicplan.public_plan'))
        return render_template('public_plan.html', locked=True, error=True), 401
    if not pw:
        return render_template('public_plan.html', locked=True, unset=True), 503
    if not session.get(SESSION_KEY):
        return render_template('public_plan.html', locked=True)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Channels are reported as presence only. Quantities and costs are deliberately not
        # selected here, so they cannot leak into the template by accident.
        cur.execute("""
            SELECT s.district_code, s.tier,
                   (SELECT max(d.seat_count) FROM districts d
                     WHERE d.full_district_code = s.district_code),
                   (SELECT max(d.county_name) FROM districts d
                     WHERE d.full_district_code = s.district_code),
                   (SELECT max(d.pvi_rating) FROM districts d
                     WHERE d.full_district_code = s.district_code),
                   (SELECT max(d.pvi) FROM districts d
                     WHERE d.full_district_code = s.district_code),
                   r.kind,
                   (SELECT string_agg(f.first_name || ' ' || f.last_name, ', ' ORDER BY f.last_name)
                      FROM filings f
                     WHERE f.election_year = 2026 AND f.office = 'State Representative'
                       AND f.district_code = s.district_code AND f.party = 'R'
                       AND f.result <> 'lost'),
                   (SELECT string_agg(DISTINCT t.grp, ',')
                      FROM district_spend_item i
                      JOIN spend_tactic t ON t.tactic_key = i.tactic_key
                     WHERE i.district_code = s.district_code AND i.qty > 0),
                   (SELECT max(p.r_seats) FROM district_past_results p
                     WHERE p.district_code = s.district_code AND p.year = 2024),
                   (SELECT string_agg(b.base, ', ' ORDER BY b.base)
                      FROM district_floterial_base b WHERE b.floterial = s.district_code),
                   (SELECT max(tt.town) FROM district_top_r_town tt
                     WHERE tt.district_code = s.district_code)
            FROM district_spend s
            LEFT JOIN district_relation r ON r.district_code = s.district_code
            WHERE s.tier IS NOT NULL
            ORDER BY 4, 1
        """)
        rows = []
        for r in cur.fetchall():
            grps = set((r[8] or '').split(',')) - {''}
            channels = [lbl for key, lbl in
                        (('mail', 'Mail'), ('text', 'Text'), ('digital', 'Digital'), ('field', 'Field'))
                        if key in grps]
            rows.append({
                'code': r[0], 'tier': r[1], 'seats': r[2] or 0, 'county': r[3],
                'rating': r[4] or '', 'pvi': float(r[5]) if r[5] is not None else None,
                'floterial': (r[6] == 'floterial'),
                'nominees': r[7] or '', 'channels': channels,
                'r_seats_2024': r[9], 'bases': r[10] or '', 'town': r[11] or '',
            })

        def key(x):
            parts = x['code'].rsplit(' ', 1)
            return (x['county'] or '', int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0)
        rows.sort(key=key)

        # A base district and the floterial over it are ONE effort: a single mail piece goes
        # into the base carrying the floterial candidate too. Listing them as separate rows
        # reads as two campaigns, and reads the floterial as getting nothing. They are grouped
        # exactly as the internal planner groups them, so both tell the same story.
        by_code = {x['code']: x for x in rows}
        cluster_of, members = {}, {}
        for x in rows:
            if not x['floterial']:
                continue
            # only bases we are actually working: one the floterial merely sits over is not a
            # joint effort, and the floterial stands on its own
            bases = [c for c in [b.strip() for b in (x['bases'] or '').split(',')]
                     if c and c in by_code]
            if not bases:
                continue
            key = x['code']
            for code in bases + [x['code']]:
                cluster_of[code] = key
            members[key] = [by_code[c] for c in bases] + [x]

        # Emit each cluster where its first member falls, so district order is unbroken.
        items, done = [], set()
        county = None
        for x in rows:
            if x['county'] != county:
                county = x['county']
                items.append({'kind': 'county', 'name': county})
            key = cluster_of.get(x['code'])
            if not key:
                items.append({'kind': 'district', 'row': x})
                continue
            if key in done:
                continue
            done.add(key)
            mem = members[key]
            label = ' + '.join(
                (m['code'].rsplit(' ', 1)[-1] + ('F' if m['floterial'] else '')) for m in mem)
            items.append({'kind': 'cluster', 'key': key,
                          'label': (mem[0]['county'] or '') + ' ' + label,
                          'seats': sum(m['seats'] for m in mem),
                          'members': mem})
        for x in rows:
            x['in_cluster'] = x['code'] in cluster_of

        summary = {
            'districts': len(rows),
            'seats': sum(x['seats'] for x in rows),
            't1': sum(1 for x in rows if x['tier'] == 1),
            't2': sum(1 for x in rows if x['tier'] == 2),
            't3': sum(1 for x in rows if x['tier'] == 3),
            'candidates': sum(len([n for n in x['nominees'].split(', ') if n]) for x in rows),
            'clusters': len(members),
            'clustered': len(cluster_of),
        }
        return render_template('public_plan.html', rows=rows, items=items,
                               summary=summary, locked=False)
    finally:
        cur.close()
        release_db_connection(conn)
