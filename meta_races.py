"""
Which race is a Meta campaign for?

CTEHR runs one campaign per district most of the time, and names it well: "Wallace | BE2
General | Meredith | Impressions | Sep-Nov 2026". But not always, and the ads under a
campaign carry more clues than its name: the ad set name, the headline, the body. All of
it is read here and every clue is scored:

    a district written out       "BE2", "Belknap 2", "Hills 12"          95
    a candidate's full name      first and last, one 2026 R candidate    92
    a town in exactly one district                                       90
    a candidate's last name, if only one candidate has it                80
    a town that sits in several districts                                50 each
    a last name several candidates share                                 45 each

Two independent clues for the same district lift it by 5, capped at 99. A district is
linked automatically only when it scores 90 or more AND nothing else scores 60 or more -
that is what "90% sure" means here. Anything under the bar is put to a person on the Meta
ads page with the clues laid out, and their answer (a district, several, or "no race") is
kept and never overwritten by the machine.

Links live in meta_campaign_district (033), one row per district; "no race" in
meta_campaign_norace (035). A campaign is the unit, not the ad: an ad inherits its campaign's
race, which is how CTEHR spends anyway.
"""
import logging
import re

from flask import Blueprint, jsonify, request
from flask_login import current_user
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

races_bp = Blueprint('races', __name__, url_prefix='/meta/races')

get_db_connection = None
release_db_connection = None


def init_meta_races(db_conn_func, db_release_func):
    global get_db_connection, release_db_connection
    get_db_connection = db_conn_func
    release_db_connection = db_release_func


# The bar. A machine link needs this much, with no serious rival.
AUTO_MIN = 90
RIVAL_MAX = 60

COUNTY_ABBR = {
    'BE': 'Belknap', 'BELK': 'Belknap', 'BELKNAP': 'Belknap',
    'CA': 'Carroll', 'CARR': 'Carroll', 'CARROLL': 'Carroll',
    'CH': 'Cheshire', 'CHES': 'Cheshire', 'CHESHIRE': 'Cheshire',
    'CO': 'Coos', 'COOS': 'Coos',
    'GR': 'Grafton', 'GRAF': 'Grafton', 'GRAFTON': 'Grafton',
    'HI': 'Hillsborough', 'HILL': 'Hillsborough', 'HILLS': 'Hillsborough', 'HILLSBOROUGH': 'Hillsborough',
    'ME': 'Merrimack', 'MERR': 'Merrimack', 'MERRIMACK': 'Merrimack',
    'RO': 'Rockingham', 'ROCK': 'Rockingham', 'ROCKINGHAM': 'Rockingham',
    'ST': 'Strafford', 'STRAF': 'Strafford', 'STRAFFORD': 'Strafford',
    'SU': 'Sullivan', 'SULL': 'Sullivan', 'SULLIVAN': 'Sullivan',
}
_CODE_RE = re.compile(r'\b([A-Za-z]{2,12})\.?[\s-]?0*(\d{1,2})\b')


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


# =============================================================================
# WHAT WE KNOW: districts, towns, candidates
# =============================================================================

def load_reference(cur):
    """Everything a clue can be matched against. One query each; a few hundred rows."""
    cur.execute("SELECT DISTINCT full_district_code, town FROM districts WHERE full_district_code IS NOT NULL")
    known, towns = set(), {}
    for r in cur.fetchall():
        code = r['full_district_code']
        known.add(code)
        town = (r['town'] or '').strip()
        if not town:
            continue
        # "Manchester Ward 3" is a town row; "Manchester" on its own is a looser clue that
        # covers every ward, so both spellings are indexed.
        towns.setdefault(town.upper(), set()).add(code)
        base = re.sub(r'\s+WARD\s+\d+$', '', town.upper())
        if base != town.upper():
            towns.setdefault(base, set()).add(code)
    cur.execute("""SELECT c.first_name, c.last_name, ces.district_code
                     FROM candidate_election_status ces
                     JOIN candidates c ON c.candidate_id = ces.candidate_id
                    WHERE ces.election_year = 2026 AND c.party = 'R'
                      AND ces.district_code IS NOT NULL AND coalesce(ces.status, '') <> 'Declined'""")
    cands = []
    for r in cur.fetchall():
        first, last = (r['first_name'] or '').strip(), (r['last_name'] or '').strip()
        if last and r['district_code'] in known:
            cands.append((first, last, r['district_code']))
    return {'known': known, 'towns': towns, 'candidates': cands}


# =============================================================================
# SCORING
# =============================================================================

def _has(text_upper, phrase):
    return re.search(r'(?<![A-Z0-9])' + re.escape(phrase.upper()) + r'(?![A-Z0-9])', text_upper) is not None


def suggest(name, extra_text, ref):
    """Scores every district the text points at. Returns [{code, score, why}], best first.

    `name` is the campaign name and counts on its own; `extra_text` is everything under it
    (ad set names, ad names, headlines, bodies) joined together. A clue in the name and the
    same clue again in an ad are one clue, not two; two DIFFERENT clues are what lift a score.
    """
    known, towns, cands = ref['known'], ref['towns'], ref['candidates']
    blob = f'{name or ""}\n{extra_text or ""}'
    up = blob.upper()
    # district -> {kind: (score, why)}; one entry per kind of clue, best of that kind kept
    hits = {}

    def add(code, kind, score, why):
        d = hits.setdefault(code, {})
        if kind not in d or d[kind][0] < score:
            d[kind] = (score, why)

    # 1. A district written out.
    for m in _CODE_RE.finditer(blob):
        county = COUNTY_ABBR.get(m.group(1).upper())
        if not county:
            continue
        code = f'{county} {int(m.group(2))}'
        if code in known:
            add(code, 'code', 95, f'"{m.group(0).strip()}" in the {"name" if m.start() < len(name or "") else "ads"}')

    # 2. Candidates. Full name beats last name; a shared last name is a weak clue.
    by_last = {}
    for first, last, code in cands:
        by_last.setdefault(last.upper(), []).append((first, last, code))
    for last_u, group in by_last.items():
        if not _has(up, last_u):
            continue
        full_hits = [(f, l, c) for f, l, c in group if f and _has(up, f'{f} {l}')]
        if full_hits:
            for f, l, c in full_hits:
                add(c, 'candidate', 92, f'candidate {f} {l}')
        elif len(group) == 1:
            f, l, c = group[0]
            add(c, 'candidate', 80, f'surname {l} (only one candidate has it)')
        else:
            for f, l, c in group:
                add(c, 'candidate', 45, f'surname {l} (shared by {len(group)} candidates)')

    # 3. Towns. Longer names first so "Manchester Ward 3" wins over "Manchester".
    for town_u in sorted(towns, key=len, reverse=True):
        if len(town_u) < 4 or not _has(up, town_u):
            continue
        codes = towns[town_u]
        pretty = town_u.title()
        if len(codes) == 1:
            add(next(iter(codes)), 'town', 90, f'town {pretty}')
        else:
            for c in codes:
                add(c, 'town', 50, f'town {pretty} (in {len(codes)} districts)')

    out = []
    for code, kinds in hits.items():
        best = max(s for s, _ in kinds.values())
        score = min(99, best + 5) if len(kinds) >= 2 else best
        why = '; '.join(w for _, w in sorted(kinds.values(), key=lambda t: -t[0]))
        out.append({'code': code, 'score': score, 'why': why})
    out.sort(key=lambda s: (-s['score'], s['code']))
    return out


def decide(suggestions):
    """The districts to link automatically, or [] when a person has to look. Several districts
    can clear the bar at once (a campaign naming two of them); one strong answer with a
    serious rival does not."""
    sure = [s for s in suggestions if s['score'] >= AUTO_MIN]
    if not sure:
        return []
    if any(RIVAL_MAX <= s['score'] < AUTO_MIN for s in suggestions):
        return []
    return sure


# =============================================================================
# READING THE CAMPAIGNS
# =============================================================================

def _campaign_texts(cur):
    """Every campaign we hold, with the words under it. Keyed by campaign id."""
    cur.execute("""SELECT campaign_id, max(campaign_name) AS name, max(ad_account_id) AS ad_account_id
                     FROM meta_insights WHERE campaign_id <> '' GROUP BY campaign_id""")
    camps = {r['campaign_id']: {'id': r['campaign_id'], 'name': r['name'] or r['campaign_id'],
                                'account_id': r['ad_account_id'], 'texts': []} for r in cur.fetchall()}
    cur.execute("""SELECT campaign_id, campaign_name, ad_account_id, name, adset_name, title, body
                     FROM meta_ads WHERE campaign_id IS NOT NULL""")
    for r in cur.fetchall():
        c = camps.setdefault(r['campaign_id'], {'id': r['campaign_id'], 'name': r['campaign_name'] or r['campaign_id'],
                                                'account_id': r['ad_account_id'], 'texts': []})
        if r['campaign_name'] and c['name'] == c['id']:
            c['name'] = r['campaign_name']
        c['texts'].extend(t for t in (r['adset_name'], r['name'], r['title'], r['body']) if t)
    cur.execute("SELECT campaign_id, name FROM meta_ad_sets WHERE campaign_id IS NOT NULL")
    for r in cur.fetchall():
        if r['campaign_id'] in camps and r['name']:
            camps[r['campaign_id']]['texts'].append(r['name'])
    return camps


def _linked(cur):
    cur.execute("""SELECT campaign_id, district_code, source, confidence, evidence
                     FROM meta_campaign_district ORDER BY campaign_id, district_code""")
    out = {}
    for r in cur.fetchall():
        out.setdefault(r['campaign_id'], []).append(dict(r))
    return out


def _norace(cur):
    cur.execute("SELECT campaign_id FROM meta_campaign_norace")
    return {r['campaign_id'] for r in cur.fetchall()}


# =============================================================================
# THE TWO OPERATIONS
# =============================================================================

def auto_attribute(cur, who='auto'):
    """Links every campaign the clues are sure about. Never touches a campaign a person has
    decided (manual rows, or "no race"); re-scores its own earlier links so a better clue
    later can improve them. Returns counts and the campaigns left for a person."""
    ref = load_reference(cur)
    camps = _campaign_texts(cur)
    linked = _linked(cur)
    norace = _norace(cur)
    done, asked = 0, []
    for cid, c in camps.items():
        rows = linked.get(cid, [])
        if cid in norace or any(r['source'] == 'manual' for r in rows):
            continue
        sugg = suggest(c['name'], '\n'.join(c['texts']), ref)
        sure = decide(sugg)
        if not sure:
            asked.append(cid)
            continue
        cur.execute("DELETE FROM meta_campaign_district WHERE campaign_id = %s AND source = 'auto'", (cid,))
        for s in sure:
            cur.execute("""INSERT INTO meta_campaign_district
                               (campaign_id, district_code, source, campaign_name, linked_by, confidence, evidence)
                           VALUES (%s, %s, 'auto', %s, %s, %s, %s)
                           ON CONFLICT (campaign_id, district_code) DO UPDATE
                               SET campaign_name = EXCLUDED.campaign_name, confidence = EXCLUDED.confidence,
                                   evidence = EXCLUDED.evidence, linked_at = now()""",
                        (cid, s['code'], c['name'], who, s['score'], s['why'][:500]))
        done += 1
    return {'linked': done, 'asked': len(asked), 'no_district': [camps[c]['name'] for c in asked]}


def set_races(cur, campaign_id, codes, who, campaign_name=None):
    """A person's answer. `codes` empty means "no race"."""
    codes = [c for c in dict.fromkeys(codes) if c]
    if not campaign_name:
        cur.execute("SELECT max(campaign_name) AS n FROM meta_insights WHERE campaign_id = %s", (campaign_id,))
        r = cur.fetchone()
        campaign_name = (r and r['n']) or campaign_id
    cur.execute("DELETE FROM meta_campaign_district WHERE campaign_id = %s", (campaign_id,))
    cur.execute("DELETE FROM meta_campaign_norace WHERE campaign_id = %s", (campaign_id,))
    if not codes:
        cur.execute("""INSERT INTO meta_campaign_norace (campaign_id, campaign_name, decided_by)
                       VALUES (%s, %s, %s)""", (campaign_id, campaign_name, who))
        return
    for code in codes:
        cur.execute("""INSERT INTO meta_campaign_district
                           (campaign_id, district_code, source, campaign_name, linked_by, confidence, evidence)
                       VALUES (%s, %s, 'manual', %s, %s, 100, 'set by hand')""",
                    (campaign_id, code, campaign_name, who))


# =============================================================================
# FOR THE PAGE
# =============================================================================

def page_data():
    """What the Meta ads page needs: the race(s) on every campaign, and the open questions
    with their clues, best first. Also every district code, for the picker."""
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        ref = load_reference(cur)
        camps = _campaign_texts(cur)
        linked = _linked(cur)
        norace = _norace(cur)
        cur.execute("""SELECT campaign_id, coalesce(sum(spend), 0) AS spend FROM meta_insights
                        WHERE level = 'campaign' GROUP BY campaign_id""")
        spend = {r['campaign_id']: float(r['spend']) for r in cur.fetchall()}
        cur.execute("SELECT id, name FROM meta_ad_accounts")
        acct = {r['id']: r['name'] for r in cur.fetchall()}
    finally:
        release_db_connection(conn)

    by_campaign = {}
    for cid, rows in linked.items():
        by_campaign[cid] = {'codes': [r['district_code'] for r in rows],
                            'source': 'manual' if any(r['source'] == 'manual' for r in rows) else 'auto',
                            'confidence': min((r['confidence'] or 0) for r in rows) if rows else None,
                            'evidence': '; '.join(r['evidence'] for r in rows if r['evidence'])}
    for cid in norace:
        by_campaign[cid] = {'codes': [], 'source': 'manual', 'confidence': 100, 'evidence': 'no race (set by hand)'}

    open_q = []
    for cid, c in camps.items():
        if cid in by_campaign:
            continue
        sugg = suggest(c['name'], '\n'.join(c['texts']), ref)[:5]
        open_q.append({'campaign_id': cid, 'campaign_name': c['name'], 'account_name': acct.get(c['account_id'], ''),
                       'spend': spend.get(cid, 0.0), 'suggestions': sugg})
    open_q.sort(key=lambda q: -q['spend'])
    return {'by_campaign': by_campaign, 'open': open_q, 'district_codes': sorted(ref['known'], key=_district_sort)}


def _district_sort(code):
    m = re.match(r'^(.*?)\s+(\d+)$', code or '')
    return (m.group(1), int(m.group(2))) if m else (code, 0)


# =============================================================================
# ROUTES
# =============================================================================

def _who():
    return getattr(current_user, 'email', None) if current_user.is_authenticated else 'admin'


def _gate(f):
    # Imported lazily: meta_ads imports this module for the page.
    from meta_ads import meta_access_required
    return meta_access_required(f)


@races_bp.route('/<campaign_id>', methods=['POST'])
def set_race(campaign_id):
    """JSON {"districts": ["Belknap 2", ...]} - empty list means no race."""
    return _gate(_set_race)(campaign_id)


def _set_race(campaign_id):
    data = request.get_json(silent=True) or {}
    codes = [str(c).strip() for c in (data.get('districts') or [])]
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        known = load_reference(cur)['known']
        bad = [c for c in codes if c not in known]
        if bad:
            return jsonify({'ok': False, 'error': f'Not a district: {", ".join(bad)}'}), 400
        set_races(cur, campaign_id, codes, _who(), data.get('campaign_name'))
        conn.commit()
        return jsonify({'ok': True, 'districts': codes})
    except Exception as e:
        conn.rollback()
        logger.exception('set race failed')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500
    finally:
        release_db_connection(conn)


@races_bp.route('/auto', methods=['POST'])
def run_auto():
    return _gate(_run_auto)()


def _run_auto():
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        res = auto_attribute(cur, _who())
        conn.commit()
        return jsonify({'ok': True, **res})
    except Exception as e:
        conn.rollback()
        logger.exception('auto attribute failed')
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500
    finally:
        release_db_connection(conn)


def attribute_after_sync():
    """Called at the end of every sync so new campaigns are linked (or queued to ask about)
    without anyone pressing anything. Never lets a failure here fail the sync."""
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        res = auto_attribute(cur, 'auto')
        conn.commit()
        return res
    except Exception as e:
        conn.rollback()
        logger.warning(f'[meta] race attribution after sync failed: {e}')
        return None
    finally:
        release_db_connection(conn)

