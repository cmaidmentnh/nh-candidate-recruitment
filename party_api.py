"""Partner API for the NH Republicans party app (/api/v1/party/*).

The party app (nhgop.1772strategies.com, nh-republicans-app) gives the state chair, HQ, region chairs and
county chairs this app's recruitment board on their phones. It calls these endpoints server to server with one
bearer key; it decides who may see what and passes a `county` and/or `town` restriction on every call made for someone without
statewide scope. This module enforces that restriction itself as well (see Scope): a read only returns districts and
candidates in that area, and a write to a candidate or district outside it is refused.

"Suggest a candidate" from any app user lands here too (POST /suggestions): the person goes on this cycle's board as a
New Recruit in their town's district, with the suggestion as a comment, so the people who work the board see it.

What it reads: districts, candidates (Republicans only), candidate_election_status, comments, activity_log,
candidate_history, filings and incumbents. The board and its cards come from get_data_and_dashboard(), the same
function the dashboard at / uses, so the numbers are the dashboard's numbers.

What it never touches: private features (secret primaries, speaker votes), the scout list, whip check-ins,
Meta and StackAdapt, spend plans, finance and billing, portal accounts, passwords, 2FA and voter records
(only a yes/no "matched to the voter file").

Writes take an `actor` ({"name", "email"}: the app user). Each one is recorded here the way this app records its
own: activity_log (user_email "<Name> (NH Republicans app) <email>"), comments.added_by and
candidates.modified_by "<Name> (NH Republicans app)", so candidate_history (written by the update trigger) names
the person too.

Auth: `Authorization: Bearer <key>` (or X-API-Key). Only the key's SHA-256 is kept, in PARTY_API_KEY_SHA256
(comma separated, so a key can be rotated). With it unset every call answers 503.

The Coordinator's recruiting sync (CTEHR-2028, src/lib/recruit/sync.ts) reads candidate_election_status and
candidates.phone1/email1 here and merges by "changed since the last sync", so a status or contact change made
through this API flows into The Coordinator like a change made on this site. Nothing here reads The Coordinator,
so there is no loop.
"""
import hashlib
import hmac
import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from flask import Blueprint, jsonify, request

from cycle import CYCLE, PRIOR, IS_INCUMBENT_SQL

logger = logging.getLogger(__name__)

party_api_bp = Blueprint('party_api', __name__, url_prefix='/api/v1/party')

_get_db = None
_release_db = None
_dashboard = None

COUNTIES = ('Belknap', 'Carroll', 'Cheshire', 'Coos', 'Grafton', 'Hillsborough', 'Merrimack', 'Rockingham',
            'Strafford', 'Sullivan')
STATUSES = ('Confirmed', 'Considering', 'Potential', 'Declined', 'New Recruit')
ACTOR_SUFFIX = ' (NH Republicans app)'

# The dashboard cards, in the dashboard's order. The first seven filter to districts; the last two to people.
DISTRICT_CARDS = ('confirmed', 'empty_seats', 'empty_districts', 'districts_with_vacancies', 'potentials',
                  'primaries', 'incumbents_running', 'incumbents_not_running')
PEOPLE_CARDS = ('incumbents_undecided', 'unmatched_voters')
CARDS = DISTRICT_CARDS + PEOPLE_CARDS

# Activity the party app may show. Portal, email, impersonation, walkbook and consult entries stay here.
RECRUIT_ACTIONS = ('candidate_added', 'candidate_created', 'candidate_copied', 'candidate_updated',
                   'candidate_merged', 'status_updated', 'contact_updated', 'comment_added', 'incumbent_toggled',
                   'district_updated', 'filing_recorded', 'voter_matched', 'candidate_suggested')

# Contact fields the app may edit, with the column's width.
CONTACT_FIELDS = {'email1': 255, 'email2': 255, 'phone1': 20, 'phone2': 20, 'address': 100, 'city': 50,
                  'zip': 10, 'signal': 50, 'twitter_x': 255, 'facebook': 255, 'instagram': 255}

HISTORY_FIELDS = ('first_name', 'last_name', 'incumbent', 'address', 'city', 'zip', 'phone1', 'phone2', 'email1',
                  'email2', 'signal', 'twitter_x', 'facebook', 'instagram')

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def init_party_api(get_db_connection, release_db_connection, get_data_and_dashboard):
    global _get_db, _release_db, _dashboard
    _get_db = get_db_connection
    _release_db = release_db_connection
    _dashboard = get_data_and_dashboard


# ---------------------------------------------------------------- errors and helpers

class ApiError(Exception):
    def __init__(self, http_status, code, detail=None, **extra):
        super().__init__(detail or code)
        self.http_status, self.code, self.detail, self.extra = http_status, code, detail, extra


@party_api_bp.errorhandler(ApiError)
def _api_error(e):
    body = {'error': e.code}
    if e.detail:
        body['detail'] = e.detail
    body.update(e.extra)
    return jsonify(body), e.http_status


def _json_value(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def county_of_code(code):
    """'Hillsborough 15' -> 'Hillsborough'. None for anything that isn't a county House district code."""
    m = re.match(r'^\s*([A-Za-z]+)\s+\d+\s*$', code or '')
    if not m:
        return None
    county = m.group(1).title()
    return county if county in COUNTIES else None


def parse_counties(value):
    """`county` from the query string or the body: one name, a comma separated list, or a list. None means the
    whole state. An unknown name is a 400, never silently the whole state."""
    if value is None or value == '' or value == []:
        return None
    items = value if isinstance(value, list) else str(value).split(',')
    out = set()
    for item in items:
        name = str(item).strip().title()
        if not name:
            continue
        if name not in COUNTIES:
            raise ApiError(400, 'bad_county', f'Unknown county: {item}')
        out.add(name)
    if not out:
        raise ApiError(400, 'bad_county', 'county was given but names no county')
    return frozenset(out)


_TOWN_WORDS = {'gt': 'grant', 'loc': 'location', 'pur': 'purchase', 'atk': 'atkinson', '&': 'and'}
_TOWN_ALIASES = {'thompson and mess purchase': 'thompson and meserves purchase'}


def norm_town(name):
    """A town name as both sides can compare it: lower case, no apostrophes or periods, '&' as 'and', and the
    districts table's abbreviations spelled out ("Low & Burbank's Gt." == "Low and Burbanks Grant")."""
    text = str(name or '').replace('\u2019', "'").replace("'", '').replace('.', ' ').replace('&', ' & ').lower()
    words = [_TOWN_WORDS.get(w, w) for w in text.split()]
    out = ' '.join(words)
    return _TOWN_ALIASES.get(out, out)


def parse_towns(value):
    """`town`: one name, a comma separated list, or a list; each a town ('Bedford') or a ward ('Manchester Ward 3').
    -> frozenset of (normalized town, ward or None). None means no town restriction."""
    if value is None or value == '' or value == []:
        return None
    items = value if isinstance(value, list) else str(value).split(',')
    out = set()
    for item in items:
        text = re.sub(r'\s+', ' ', str(item)).strip()
        if not text:
            continue
        m = re.match(r'^(.*?)\s+Ward\s+(\d+)$', text, re.I)
        out.add((norm_town(m.group(1)), int(m.group(2))) if m else (norm_town(text), None))
    if not out:
        raise ApiError(400, 'bad_town', 'town was given but names no town')
    return frozenset(out)


class Scope:
    """Where a call may read and write: the whole state, or the districts of some counties and/or towns. A town
    covers every House district it votes in, base and floterial, from the districts table's town and ward rows
    (never geocoded); a ward covers that ward's districts. county and town together = the districts of either."""

    def __init__(self, counties=None, towns=None, codes=None):
        self.counties, self.towns, self.codes = counties, towns, codes

    @property
    def statewide(self):
        return self.codes is None

    def allows(self, code):
        return self.codes is None or (code or '') in self.codes

    def json(self):
        towns = None
        if self.towns is not None:
            towns = sorted(f'{t.title()} Ward {w}' if w else t.title() for t, w in self.towns)
        return {'statewide': self.statewide, 'counties': sorted(self.counties) if self.counties else None,
                'towns': towns, 'districts': len(self.codes) if self.codes is not None else None}


def district_codes(rows, counties, towns):
    """districts table rows (full_district_code, town, ward) -> the codes in these counties or towns. Pure.
    An unknown town is a 400, never silently nothing."""
    codes, seen = set(), set()
    for code, town, ward in rows:
        t = norm_town(town)
        seen.add(t)
        if counties and county_of_code(code) in counties:
            codes.add(code)
        if towns and ((t, None) in towns or (t, ward or None) in towns):
            codes.add(code)
    unknown = sorted(t for t, _ in (towns or ()) if t not in seen)
    if unknown:
        raise ApiError(400, 'bad_town', f'Unknown town: {", ".join(unknown)}')
    return frozenset(codes)


def _request_scope(body=None):
    """The call's scope from `county` and `town` (query string), or a write body's `area`."""
    # Writes carry it as "area": {"county": [...], "town": [...]} ("town" alone in a write body is the recruit's own
    # town). A top-level "county" is accepted too.
    if body is not None and isinstance(body.get('area'), dict):
        cvals, tvals = body['area'].get('county'), body['area'].get('town')
    elif body is not None and 'county' in body:
        cvals, tvals = body.get('county'), None
    else:
        cl, tl = request.args.getlist('county'), request.args.getlist('town')
        cvals = cl[0] if len(cl) == 1 else cl
        tvals = tl[0] if len(tl) == 1 else tl
    counties, towns = parse_counties(cvals), parse_towns(tvals)
    if counties is None and towns is None:
        return Scope()
    with _Db() as db:
        db.cur.execute('SELECT full_district_code, town, ward FROM districts')
        rows = db.cur.fetchall()
    return Scope(counties, towns, district_codes(rows, counties, towns))


def actor_labels(actor):
    """{'name', 'email'} -> (short label for 50-character columns, long label for activity_log.user_email)."""
    if not isinstance(actor, dict):
        raise ApiError(400, 'actor_required', 'Every write needs actor {name, email}.')
    name = re.sub(r'\s+', ' ', str(actor.get('name') or '')).strip()
    email = str(actor.get('email') or '').strip()
    if not name or not EMAIL_RE.match(email) or len(email) > 200:
        raise ApiError(400, 'actor_required', 'Every write needs actor {name, email}.')
    short = name[:50 - len(ACTOR_SUFFIX)].strip() + ACTOR_SUFFIX
    return short, f'{short} <{email}>'[:255]


def _clean(text, limit, field):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if len(text) > limit:
        raise ApiError(400, 'too_long', f'{field} is longer than {limit} characters')
    return text or None


def _key_hashes():
    raw = os.environ.get('PARTY_API_KEY_SHA256') or ''
    return [h.strip().lower() for h in raw.split(',') if re.fullmatch(r'[0-9a-fA-F]{64}', h.strip())]


def _presented_key():
    k = request.headers.get('X-API-Key', '').strip()
    if k:
        return k
    auth = request.headers.get('Authorization', '').strip()
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return ''


def require_party_key(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        hashes = _key_hashes()
        if not hashes:
            return jsonify({'error': 'not_configured', 'detail': 'PARTY_API_KEY_SHA256 is not set.'}), 503
        key = _presented_key()
        if not key:
            return jsonify({'error': 'missing_api_key'}), 401
        digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
        if not any(hmac.compare_digest(digest, h) for h in hashes):
            return jsonify({'error': 'invalid_api_key'}), 401
        return fn(*a, **kw)
    return wrapper


def rate_key():
    """Rate limits are per app user (the app sends X-Party-Actor), so one busy chair can't use up everyone's."""
    from flask_limiter.util import get_remote_address
    actor = (request.headers.get('X-Party-Actor') or '').strip().lower()[:200]
    return 'party:' + (actor or get_remote_address())


class _Db:
    def __enter__(self):
        self.conn = _get_db()
        self.cur = self.conn.cursor()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.conn.rollback()
            self.cur.close()
        finally:
            _release_db(self.conn)

    def all(self, sql, params=()):
        self.cur.execute(sql, params)
        cols = [d[0] for d in self.cur.description]
        return [dict(zip(cols, r)) for r in self.cur.fetchall()]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None


def _log(db, action, description, candidate_id, long_label):
    db.cur.execute("""INSERT INTO activity_log (action_type, description, candidate_id, user_email)
                      VALUES (%s, %s, %s, %s)""", (action, description, candidate_id, long_label))


# ---------------------------------------------------------------- the board (the dashboard at /)

def build_board(groups, dashboard, extra, codes=None):
    """get_data_and_dashboard()'s output -> the board JSON, limited to the district `codes` when given.

    `extra` is {candidate_id: {'town', 'is_running'}} for this cycle's candidates. Pure, for tests."""
    all_cur = set()
    for dist_dict in groups.values():
        for info in dist_dict.values():
            for c in info['cand_cur']:
                all_cur.add(c['candidate_id'])

    districts = []
    for group, dist_dict in groups.items():
        for fdc, info in dist_dict.items():
            county = county_of_code(fdc)
            if codes is not None and fdc not in codes:
                continue
            cands = []
            for c in info['cand_cur']:
                x = extra.get(c['candidate_id'], {})
                cands.append({'id': c['candidate_id'], 'name': c['name'], 'town': x.get('town'),
                              'status': c['status'], 'incumbent': bool(c['incumbent']),
                              'is_running': x.get('is_running', (c['status'] or '').upper() != 'DECLINED'),
                              'filed': bool(c.get('filed'))})
            sitting = [{'id': c['candidate_id'], 'name': c['name']} for c in info['cand_prior']
                       if c['incumbent'] and c['candidate_id'] not in all_cur]
            confirmed = sum(1 for c in cands if (c['status'] or '').upper() == 'CONFIRMED')
            active = sum(1 for c in cands if (c['status'] or '').upper() != 'DECLINED')
            seats = info['seat_count']
            districts.append({
                'code': fdc, 'county': county, 'group': group, 'number': int(fdc.rsplit(' ', 1)[1]),
                'seats': seats, 'towns': list(info['towns']), 'pvi': _json_value(info['pvi']),
                'rating': info['pvi_rating'], 'confirmed': confirmed, 'open_seats': max(0, seats - confirmed),
                'active': active, 'primary': confirmed > seats, 'empty': active == 0,
                'candidates': cands, 'sitting_no_record': sitting})
    districts.sort(key=lambda d: (d['county'] or '', d['number']))
    return districts


def summarize(districts):
    """The dashboard cards recomputed over a set of districts, with get_data_and_dashboard()'s rules. For the
    whole state this equals the dashboard's own totals (tested)."""
    s = {k: 0 for k in DISTRICT_CARDS + ('incumbents_undecided',)}
    seats_total = seats_filled = 0
    for d in districts:
        seats = d['seats']
        st = [(c['status'] or '').upper() for c in d['candidates']]
        conf = st.count('CONFIRMED')
        active = sum(1 for x in st if x != 'DECLINED')
        seats_total += seats
        seats_filled += min(conf, seats)
        s['confirmed'] += conf
        s['empty_seats'] += max(0, seats - conf)
        s['empty_districts'] += active == 0
        s['districts_with_vacancies'] += active < seats
        s['primaries'] += conf > seats
        s['potentials'] += sum(1 for x in st if x in ('CONSIDERING', 'POTENTIAL'))
        for c, x in zip(d['candidates'], st):
            if c['incumbent'] and x == 'CONFIRMED':
                s['incumbents_running'] += 1
            elif c['incumbent'] and x == 'DECLINED':
                s['incumbents_not_running'] += 1
            elif c['incumbent']:
                s['incumbents_undecided'] += 1
        s['incumbents_undecided'] += len(d['sitting_no_record'])
    s['total_seats'] = seats_total
    s['total_districts'] = len(districts)
    s['seats_filled'] = seats_filled
    return s


def _cycle_extra():
    with _Db() as db:
        rows = db.all("""SELECT c.candidate_id, c.city, ces.is_running
                         FROM candidate_election_status ces JOIN candidates c ON c.candidate_id = ces.candidate_id
                         WHERE ces.election_year = %s AND c.party = 'R'""", (CYCLE,))
    return {r['candidate_id']: {'town': r['city'], 'is_running': bool(r['is_running'])} for r in rows}


def _scoped_counts(codes):
    """Filed and not-matched-to-the-voter-file counts, as the dashboard counts them, in these districts."""
    with _Db() as db:
        filed = db.one("""SELECT COUNT(DISTINCT candidate_id) AS n FROM filings
                          WHERE election_year = %s AND office = 'State Representative' AND party = 'R'
                            AND candidate_id IS NOT NULL AND district_code = ANY(%s)""", (CYCLE, list(codes)))['n']
        unmatched = db.one("""SELECT COUNT(DISTINCT c.candidate_id) AS n
                              FROM candidates c JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                              WHERE ces.election_year = %s AND c.voter_id IS NULL AND ces.status != 'Declined'
                                AND ces.district_code = ANY(%s)""", (CYCLE, list(codes)))['n']
    return filed or 0, unmatched or 0


def _load_board(scope):
    groups, dashboard, _stats = _dashboard()
    if not groups:
        # get_data_and_dashboard() logs and returns nothing when the database fails; never serve that as data.
        raise ApiError(503, 'unavailable', 'The recruitment data could not be read. Try again shortly.')
    districts = build_board(groups, dashboard, _cycle_extra(), scope.codes)
    if scope.statewide:
        summary = {k: dashboard[k]['total'] for k in DISTRICT_CARDS + ('incumbents_undecided',)}
        summary.update(total_seats=dashboard['TOTAL_SEATS'], total_districts=dashboard['TOTAL_DISTRICTS'],
                       seats_filled=sum(min(d['confirmed'], d['seats']) for d in districts),
                       filed=dashboard['filed']['total'], unmatched_voters=dashboard['unmatched_voters']['total'])
    else:
        summary = summarize(districts)
        summary['filed'], summary['unmatched_voters'] = _scoped_counts(scope.codes)
    return districts, dashboard, summary


@party_api_bp.route('/board', methods=['GET'])
@require_party_key
def board():
    scope = _request_scope()
    districts, _dash, summary = _load_board(scope)
    return jsonify({'cycle': CYCLE, 'scope': scope.json(), 'summary': summary,
                    'statuses': list(STATUSES), 'districts': districts})


@party_api_bp.route('/districts', methods=['GET'])
@require_party_key
def districts():
    """Every House district (for pickers): code, county, seats, towns/wards. From the districts table only."""
    scope = _request_scope()
    with _Db() as db:
        rows = db.all("""SELECT full_district_code, county_name, town, ward, seat_count, pvi, pvi_rating
                         FROM districts ORDER BY county_name, full_district_code, town, ward""")
    out = {}
    for r in rows:
        code = r['full_district_code']
        if not scope.allows(code):
            continue
        d = out.setdefault(code, {'code': code, 'county': county_of_code(code), 'number': int(code.rsplit(' ', 1)[1]),
                                  'seats': r['seat_count'], 'pvi': _json_value(r['pvi']), 'rating': r['pvi_rating'],
                                  'towns': []})
        d['towns'].append(f"{r['town']} Ward {r['ward']}" if r['ward'] else r['town'])
    items = sorted(out.values(), key=lambda d: (d['county'] or '', d['number']))
    return jsonify({'cycle': CYCLE, 'scope': scope.json(), 'count': len(items), 'districts': items})


@party_api_bp.route('/filter', methods=['GET'])
@require_party_key
def filter_view():
    """One dashboard card's list: districts for the district cards, people for incumbents_undecided and
    unmatched_voters (the same queries as /filter on this site)."""
    category = (request.args.get('category') or '').strip()
    if category not in CARDS:
        raise ApiError(400, 'bad_category', f'category must be one of {", ".join(CARDS)}')
    scope = _request_scope()
    districts, dashboard, summary = _load_board(scope)
    if category in DISTRICT_CARDS:
        codes = {fdc for _g, fdc in dashboard[category]['districts']}
        items = [d for d in districts if d['code'] in codes]
        return jsonify({'cycle': CYCLE, 'category': category, 'scope': scope.json(),
                        'total': summary[category], 'districts': items})
    with _Db() as db:
        if category == 'unmatched_voters':
            rows = db.all(f"""
                SELECT c.candidate_id AS id, c.first_name, c.last_name, c.city AS town, ces.district_code AS district,
                       ces.status, {CYCLE} AS year
                FROM candidates c JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = {CYCLE} AND c.voter_id IS NULL AND ces.status != 'Declined'
                ORDER BY c.last_name, c.first_name""")
        else:
            rows = db.all(f"""
                SELECT c.candidate_id AS id, c.first_name, c.last_name, c.city AS town, ces.district_code AS district,
                       ces.status, {CYCLE} AS year
                FROM candidates c JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = {CYCLE} AND {IS_INCUMBENT_SQL} AND c.party = 'R'
                  AND ces.status NOT IN ('Confirmed', 'Declined')
                ORDER BY c.last_name, c.first_name""")
            rows += db.all(f"""
                SELECT i.candidate_id AS id, split_part(i.name, ' ', 1) AS first_name,
                       regexp_replace(i.name, '^\\S+\\s*', '') AS last_name, NULL AS town,
                       i.district_code AS district, 'No {CYCLE} record' AS status, {PRIOR} AS year
                FROM incumbents i
                WHERE i.election_year = {PRIOR} AND i.party = 'R'
                  AND NOT EXISTS (SELECT 1 FROM candidate_election_status ces2
                                  WHERE ces2.candidate_id = i.candidate_id AND ces2.election_year = {CYCLE})
                ORDER BY i.district_code, i.name""")
    people = [{'id': r['id'], 'name': f"{r['first_name'] or ''} {r['last_name'] or ''}".strip(), 'town': r['town'],
               'district': r['district'], 'county': county_of_code(r['district']), 'status': r['status'],
               'year': r['year']}
              for r in rows if scope.allows(r['district'])]
    return jsonify({'cycle': CYCLE, 'category': category, 'scope': scope.json(),
                    'total': summary[category], 'people': people})


# ---------------------------------------------------------------- one candidate

def _home_district(db, candidate_id):
    """This cycle's district, else the latest one on file."""
    row = db.one("""SELECT district_code, election_year FROM candidate_election_status
                    WHERE candidate_id = %s AND district_code IS NOT NULL AND district_code <> ''
                    ORDER BY (election_year = %s) DESC, election_year DESC, status_id DESC LIMIT 1""",
                 (candidate_id, CYCLE))
    return (row['district_code'], row['election_year']) if row else (None, None)


def _load_candidate(db, candidate_id, scope):
    c = db.one(f"""SELECT c.candidate_id, c.first_name, c.last_name, c.party, c.incumbent AS incumbent_flag,
                          {IS_INCUMBENT_SQL} AS sitting, c.voter_id IS NOT NULL AS voter_matched
                   FROM candidates c WHERE c.candidate_id = %s AND c.party = 'R'""", (candidate_id,))
    if not c:
        raise ApiError(404, 'not_found', 'No Republican candidate with that id')
    district, year = _home_district(db, candidate_id)
    if not scope.allows(district):
        raise ApiError(403, 'out_of_scope', 'That candidate is outside the area for this call')
    c['district'], c['district_year'] = district, year
    return c


def _history_changes(rows):
    """candidate_history snapshots (newest first) -> what each save changed."""
    out = []
    for i, r in enumerate(rows):
        prev = rows[i + 1] if i + 1 < len(rows) else None
        if prev is None:
            changed = {f: _json_value(r[f]) for f in HISTORY_FIELDS if r[f] not in (None, '')}
        else:
            changed = {f: _json_value(r[f]) for f in HISTORY_FIELDS if (r[f] or None) != (prev[f] or None)}
        if prev is not None and not changed:
            continue
        out.append({'at': _json_value(r['changed_at']), 'by': r['changed_by'], 'note': r['change_comment'],
                    'changed': changed, 'first': prev is None})
    return out


@party_api_bp.route('/candidates/<int:candidate_id>', methods=['GET'])
@require_party_key
def candidate_detail(candidate_id):
    scope = _request_scope()
    with _Db() as db:
        base = _load_candidate(db, candidate_id, scope)
        c = db.one("""SELECT address, city, zip, email, email1, email2, phone1, phone1_type, phone2, phone2_type,
                             signal, twitter_x, facebook, instagram, website_url, facebook_url, twitter_url,
                             instagram_url, photo_url, created_at, created_by, modified_at, modified_by
                      FROM candidates WHERE candidate_id = %s""", (candidate_id,))
        statuses = db.all("""SELECT election_year AS year, status, district_code AS district, is_running, added_by,
                                    added_at
                             FROM candidate_election_status WHERE candidate_id = %s
                             ORDER BY election_year DESC, status_id DESC""", (candidate_id,))
        filed = db.one("""SELECT district_code, filed_at FROM filings
                          WHERE candidate_id = %s AND election_year = %s AND office = 'State Representative'
                          ORDER BY filed_at DESC NULLS LAST, created_at DESC LIMIT 1""", (candidate_id, CYCLE))
        comments = db.all("""SELECT comment_id AS id, comment_text AS text, added_by AS "by", added_at AS at
                             FROM comments WHERE candidate_id = %s ORDER BY added_at DESC, comment_id DESC""",
                          (candidate_id,))
        activity = db.all("""SELECT log_id AS id, action_type AS action, description, user_email AS "by",
                                    created_at AS at
                             FROM activity_log WHERE candidate_id = %s AND action_type = ANY(%s)
                             ORDER BY created_at DESC, log_id DESC LIMIT 100""",
                          (candidate_id, list(RECRUIT_ACTIONS)))
        hist = db.all("""SELECT * FROM candidate_history WHERE candidate_id = %s
                         ORDER BY changed_at DESC, history_id DESC LIMIT 200""", (candidate_id,))

    cur = next((s for s in statuses if s['year'] == CYCLE), None)
    emails = [e for e in dict.fromkeys(x.strip() for x in (c['email1'], c['email2'], c['email']) if x and x.strip())]
    phones = [{'number': c['phone1'], 'type': c['phone1_type'], 'field': 'phone1'} if c['phone1'] else None,
              {'number': c['phone2'], 'type': c['phone2_type'], 'field': 'phone2'} if c['phone2'] else None]
    out = {
        'id': candidate_id, 'cycle': CYCLE,
        'name': f"{base['first_name'] or ''} {base['last_name'] or ''}".strip(),
        'first_name': base['first_name'], 'last_name': base['last_name'], 'party': base['party'],
        'district': cur['district'] if cur else base['district'],
        'county': county_of_code(cur['district'] if cur else base['district']),
        'status': cur['status'] if cur else None, 'is_running': bool(cur['is_running']) if cur else None,
        'in_cycle': cur is not None,
        'incumbent': bool(base['sitting']), 'incumbent_flag': bool(base['incumbent_flag']),
        'voter_matched': bool(base['voter_matched']),
        'filed': bool(filed), 'filed_at': _json_value(filed['filed_at']) if filed else None,
        'town': c['city'], 'photo_url': c['photo_url'],
        'contact': {
            'emails': emails, 'email1': c['email1'], 'email2': c['email2'],
            'phones': [p for p in phones if p], 'phone1': c['phone1'], 'phone2': c['phone2'],
            'address': c['address'], 'city': c['city'], 'zip': c['zip'], 'signal': c['signal'],
            'twitter_x': c['twitter_x'], 'facebook': c['facebook'], 'instagram': c['instagram'],
            'website_url': c['website_url'],
        },
        'statuses': [{k: _json_value(v) for k, v in s.items()} for s in statuses],
        'comments': [{k: _json_value(v) for k, v in x.items()} for x in comments],
        'activity': [{k: _json_value(v) for k, v in x.items()} for x in activity],
        'history': _history_changes(hist),
        'created': {'at': _json_value(c['created_at']), 'by': c['created_by']},
        'modified': {'at': _json_value(c['modified_at']), 'by': c['modified_by']},
    }
    return jsonify(out)


# ---------------------------------------------------------------- writes

def _body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(400, 'bad_request', 'Send a JSON object.')
    return body


def _district_exists(db, code):
    return db.one('SELECT full_district_code FROM districts WHERE full_district_code = %s LIMIT 1', (code,)) is not None


def _cycle_row(db, candidate_id):
    return db.one("""SELECT status_id, status, district_code FROM candidate_election_status
                     WHERE candidate_id = %s AND election_year = %s ORDER BY status_id LIMIT 1""",
                  (candidate_id, CYCLE))


def split_name(body):
    first = _clean(body.get('first_name'), 50, 'first_name')
    last = _clean(body.get('last_name'), 50, 'last_name')
    if not last and body.get('name'):
        words = re.sub(r'\s+', ' ', str(body['name'])).strip().split(' ')
        first, last = (' '.join(words[:-1]) or None, words[-1]) if len(words) > 1 else (None, words[0])
        first = _clean(first, 50, 'first_name')
        last = _clean(last, 50, 'last_name')
    if not first or not last:
        raise ApiError(400, 'name_required', 'A first and last name are required.')
    return first, last


@party_api_bp.route('/recruits', methods=['POST'])
@require_party_key
def add_recruit():
    """Add a recruit for this cycle, as /add_candidate_inline does: an existing Republican with the same first
    and last name gets this cycle's status; anyone else is a new candidate."""
    body = _body()
    short, long_ = actor_labels(body.get('actor'))
    scope = _request_scope(body)
    first, last = split_name(body)
    district = _clean(body.get('district'), 50, 'district')
    status = (body.get('status') or 'New Recruit').strip()
    if status not in STATUSES:
        raise ApiError(400, 'bad_status', f'status must be one of {", ".join(STATUSES)}')
    town = _clean(body.get('town'), 50, 'town')
    phone = _clean(body.get('phone'), 20, 'phone')
    email = _clean(body.get('email'), 255, 'email')
    note = (str(body.get('note') or '').strip() or None)
    if note and len(note) > 5000:
        raise ApiError(400, 'too_long', 'note is longer than 5000 characters')
    if email and not EMAIL_RE.match(email):
        raise ApiError(400, 'bad_email', 'That email address does not look right.')
    if not district:
        raise ApiError(400, 'district_required', 'Pick a district.')
    if not scope.allows(district):
        raise ApiError(403, 'out_of_scope', 'That district is outside the area for this call')
    with _Db() as db:
        if not _district_exists(db, district):
            raise ApiError(400, 'bad_district', f'Unknown district: {district}')
        existing = db.one("""SELECT candidate_id, city, phone1, email1 FROM candidates
                             WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s) AND party = 'R'
                             ORDER BY candidate_id LIMIT 1""", (first, last))
        if existing:
            cid = existing['candidate_id']
            row = _cycle_row(db, cid)
            if row:
                raise ApiError(409, 'already_in_cycle', f'{first} {last} is already on the {CYCLE} board',
                               candidate_id=cid, district=row['district_code'], status=row['status'])
            fills = {k: v for k, v in (('city', town), ('phone1', phone), ('email1', email))
                     if v and not existing[k]}
            if fills:
                sets = ', '.join(f'{k} = %s' for k in fills)
                db.cur.execute(f"UPDATE candidates SET {sets}, modified_by = %s, modified_at = NOW() "
                               "WHERE candidate_id = %s", (*fills.values(), short, cid))
            created = False
        else:
            db.cur.execute("""INSERT INTO candidates (first_name, last_name, party, city, phone1, email1, created_by,
                                                      modified_by, created_at, modified_at)
                              VALUES (%s, %s, 'R', %s, %s, %s, %s, %s, NOW(), NOW()) RETURNING candidate_id""",
                           (first, last, town, phone, email, short, short))
            cid = db.cur.fetchone()[0]
            created = True
        db.cur.execute("""INSERT INTO candidate_election_status (candidate_id, election_year, status, is_running,
                                                                 added_by, district_code)
                          VALUES (%s, %s, %s, %s, %s, %s)""",
                       (cid, CYCLE, status, status != 'Declined', short, district))
        if note:
            db.cur.execute("INSERT INTO comments (candidate_id, comment_text, added_by) VALUES (%s, %s, %s)",
                           (cid, note, short))
        suffix = '' if created else ' (existing candidate)'
        _log(db, 'candidate_added', f'Added {first} {last} to {district} ({status}){suffix}', cid, long_)
        db.conn.commit()
    return jsonify({'ok': True, 'candidate_id': cid, 'created': created, 'district': district,
                    'status': status, 'cycle': CYCLE}), 201


def _write_target(candidate_id, body):
    short, long_ = actor_labels(body.get('actor'))
    scope = _request_scope(body)
    return short, long_, scope


@party_api_bp.route('/candidates/<int:candidate_id>/status', methods=['POST'])
@require_party_key
def update_status(candidate_id):
    body = _body()
    short, long_, scope = _write_target(candidate_id, body)
    status = (body.get('status') or '').strip()
    if status not in STATUSES:
        raise ApiError(400, 'bad_status', f'status must be one of {", ".join(STATUSES)}')
    with _Db() as db:
        c = _load_candidate(db, candidate_id, scope)
        row = _cycle_row(db, candidate_id)
        if not row:
            raise ApiError(404, 'not_in_cycle', f'Not on the {CYCLE} board. Add them as a recruit first.')
        if row['status'] == status:
            return jsonify({'ok': True, 'status': status, 'changed': False})
        db.cur.execute("""UPDATE candidate_election_status SET status = %s, is_running = %s
                          WHERE candidate_id = %s AND election_year = %s""",
                       (status, status != 'Declined', candidate_id, CYCLE))
        _log(db, 'status_updated', f"Changed {c['first_name']} {c['last_name']} status to {status}", candidate_id, long_)
        db.conn.commit()
    return jsonify({'ok': True, 'status': status, 'previous': row['status'], 'changed': True})


@party_api_bp.route('/candidates/<int:candidate_id>/comments', methods=['POST'])
@require_party_key
def add_comment(candidate_id):
    body = _body()
    short, long_, scope = _write_target(candidate_id, body)
    text = str(body.get('text') or body.get('comment') or '').strip()
    if not text:
        raise ApiError(400, 'text_required', 'Comment text required')
    if len(text) > 5000:
        raise ApiError(400, 'too_long', 'Comments are limited to 5000 characters')
    with _Db() as db:
        _load_candidate(db, candidate_id, scope)
        db.cur.execute("""INSERT INTO comments (candidate_id, comment_text, added_by) VALUES (%s, %s, %s)
                          RETURNING comment_id, added_at""", (candidate_id, text, short))
        cid, at = db.cur.fetchone()
        _log(db, 'comment_added', f'Quick comment: {text[:50]}', candidate_id, long_)
        db.conn.commit()
    return jsonify({'ok': True, 'comment': {'id': cid, 'text': text, 'by': short, 'at': _json_value(at)}}), 201


@party_api_bp.route('/candidates/<int:candidate_id>/contact', methods=['POST'])
@require_party_key
def update_contact(candidate_id):
    """Set any of CONTACT_FIELDS; an empty string clears one. candidate_history records the save."""
    body = _body()
    short, long_, scope = _write_target(candidate_id, body)
    fields = body.get('fields') if isinstance(body.get('fields'), dict) else {
        k: v for k, v in body.items() if k in CONTACT_FIELDS}
    unknown = set(fields) - set(CONTACT_FIELDS)
    if unknown:
        raise ApiError(400, 'bad_field', f'Not editable here: {", ".join(sorted(unknown))}')
    if not fields:
        raise ApiError(400, 'no_fields', 'No fields to update')
    values = {}
    for k, v in fields.items():
        values[k] = _clean(v, CONTACT_FIELDS[k], k)
        if k.startswith('email') and values[k] and not EMAIL_RE.match(values[k]):
            raise ApiError(400, 'bad_email', f'{k} does not look like an email address')
    with _Db() as db:
        _load_candidate(db, candidate_id, scope)
        sets = ', '.join(f'{k} = %s' for k in values)
        db.cur.execute(f'UPDATE candidates SET {sets}, modified_by = %s, modified_at = NOW() WHERE candidate_id = %s',
                       (*values.values(), short, candidate_id))
        _log(db, 'contact_updated', f"Updated contact info ({', '.join(sorted(values))})", candidate_id, long_)
        db.conn.commit()
    return jsonify({'ok': True, 'updated': sorted(values)})


@party_api_bp.route('/candidates/<int:candidate_id>/incumbent', methods=['POST'])
@require_party_key
def toggle_incumbent(candidate_id):
    """Flips candidates.incumbent, like /api/toggle_incumbent. The dashboard's incumbent counts come from the
    incumbents table (last general's winners), not this flag."""
    body = _body()
    short, long_, scope = _write_target(candidate_id, body)
    with _Db() as db:
        _load_candidate(db, candidate_id, scope)
        db.cur.execute("""UPDATE candidates SET incumbent = NOT COALESCE(incumbent, FALSE), modified_by = %s,
                                 modified_at = NOW()
                          WHERE candidate_id = %s RETURNING incumbent""", (short, candidate_id))
        new_val = db.cur.fetchone()[0]
        _log(db, 'incumbent_toggled', f'Set incumbent = {new_val}', candidate_id, long_)
        db.conn.commit()
    return jsonify({'ok': True, 'incumbent': new_val})


@party_api_bp.route('/candidates/<int:candidate_id>/district', methods=['POST'])
@require_party_key
def move_district(candidate_id):
    """Move this cycle's record to another district. Both districts must be inside the call's area."""
    body = _body()
    short, long_, scope = _write_target(candidate_id, body)
    new = _clean(body.get('district'), 50, 'district')
    if not new:
        raise ApiError(400, 'district_required', 'District is required')
    if not scope.allows(new):
        raise ApiError(403, 'out_of_scope', 'That district is outside the area for this call')
    with _Db() as db:
        c = _load_candidate(db, candidate_id, scope)
        if not _district_exists(db, new):
            raise ApiError(400, 'bad_district', f'Unknown district: {new}')
        row = _cycle_row(db, candidate_id)
        if not row:
            raise ApiError(404, 'not_in_cycle', f'Not on the {CYCLE} board. Add them as a recruit first.')
        if row['district_code'] == new:
            return jsonify({'ok': True, 'district': new, 'changed': False})
        db.cur.execute("""UPDATE candidate_election_status SET district_code = %s
                          WHERE candidate_id = %s AND election_year = %s""", (new, candidate_id, CYCLE))
        _log(db, 'district_updated', f"Moved {c['first_name']} {c['last_name']} from {row['district_code']} to {new}",
             candidate_id, long_)
        db.conn.commit()
    return jsonify({'ok': True, 'district': new, 'previous': row['district_code'], 'changed': True})


def base_district_for(db, town_text):
    """A town or ward -> its one base (non-floterial) House district, from the districts table. 400 when the town is
    unknown, or is a city given without its ward (several base districts)."""
    spec = parse_towns(town_text)
    if not spec or len(spec) != 1:
        raise ApiError(400, 'town_required', 'Give one town (or city ward).')
    (town, ward), = spec
    rows = db.all("""SELECT DISTINCT d.full_district_code AS code, d.town, d.ward
                     FROM districts d
                     WHERE NOT EXISTS (SELECT 1 FROM district_relation r
                                       WHERE r.district_code = d.full_district_code AND r.kind = 'floterial')""")
    codes = sorted({r['code'] for r in rows if norm_town(r['town']) == town and (ward is None or (r['ward'] or None) == ward)})
    if not codes:
        raise ApiError(400, 'bad_town', f'Unknown town: {town_text}')
    if len(codes) > 1:
        raise ApiError(400, 'ward_required', f'{town_text} has several districts: give the ward.', districts=codes)
    return codes[0]


@party_api_bp.route('/suggestions', methods=['POST'])
@require_party_key
def suggest():
    """"Suggest a candidate" from any party app user. The person goes on this cycle's board as a New Recruit in their
    town's base district (or stays where they are if already on it), with the suggestion as a comment naming who
    suggested it. The same name match as /recruits."""
    body = _body()
    short, long_ = actor_labels(body.get('actor'))
    first, last = split_name(body)
    town = _clean(body.get('town'), 60, 'town')
    why = str(body.get('why') or '').strip()
    office = _clean(body.get('office'), 120, 'office')
    phone = _clean(body.get('phone'), 20, 'phone')
    email = _clean(body.get('email'), 255, 'email')
    if not town or not why:
        raise ApiError(400, 'bad_request', 'town and why are required')
    if len(why) > 4000:
        raise ApiError(400, 'too_long', 'why is longer than 4000 characters')
    if email and not EMAIL_RE.match(email):
        email = None
    base_town = re.sub(r'\s+Ward\s+\d+$', '', town, flags=re.I)[:50]
    who = body['actor']
    who_name, who_email = re.sub(r'\s+', ' ', str(who['name'])).strip(), str(who['email']).strip()
    note = (f"Suggested in the NH Republicans app by {who_name} <{who_email}>."
            + (f" Office: {office}." if office else '') + f" Why: {why}")
    with _Db() as db:
        district = base_district_for(db, town)
        existing = db.one("""SELECT candidate_id, city, phone1, email1 FROM candidates
                             WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s) AND party = 'R'
                             ORDER BY candidate_id LIMIT 1""", (first, last))
        row = _cycle_row(db, existing['candidate_id']) if existing else None
        if existing:
            cid = existing['candidate_id']
            fills = {k: v for k, v in (('city', base_town), ('phone1', phone), ('email1', email)) if v and not existing[k]}
            if fills:
                sets = ', '.join(f'{k} = %s' for k in fills)
                db.cur.execute(f"UPDATE candidates SET {sets}, modified_by = %s, modified_at = NOW() "
                               "WHERE candidate_id = %s", (*fills.values(), short, cid))
        else:
            db.cur.execute("""INSERT INTO candidates (first_name, last_name, party, city, phone1, email1, created_by,
                                                      modified_by, created_at, modified_at)
                              VALUES (%s, %s, 'R', %s, %s, %s, %s, %s, NOW(), NOW()) RETURNING candidate_id""",
                           (first, last, base_town, phone, email, short, short))
            cid = db.cur.fetchone()[0]
        if not row:
            db.cur.execute("""INSERT INTO candidate_election_status (candidate_id, election_year, status, is_running,
                                                                     added_by, district_code)
                              VALUES (%s, %s, 'New Recruit', TRUE, %s, %s)""", (cid, CYCLE, short, district))
        db.cur.execute("INSERT INTO comments (candidate_id, comment_text, added_by) VALUES (%s, %s, %s)",
                       (cid, note, short))
        where = row['district_code'] if row else district
        _log(db, 'candidate_suggested', f'{first} {last} suggested for {where} (New Recruit)' if not row
             else f'{first} {last} suggested again (already on the board in {where})', cid, long_)
        db.conn.commit()
    return jsonify({'ok': True, 'candidate_id': cid, 'district': where, 'status': row['status'] if row else 'New Recruit',
                    'already_on_board': bool(row), 'created': not existing, 'cycle': CYCLE}), 201


# ---------------------------------------------------------------- activity and search

@party_api_bp.route('/activity', methods=['GET'])
@require_party_key
def activity():
    """Recent recruitment activity (newest first). With county or town, only candidates whose district this cycle is
    in that area. `before` (a log id) pages back."""
    scope = _request_scope()
    limit = max(1, min(request.args.get('limit', 50, type=int) or 50, 200))
    before = request.args.get('before', type=int)
    where, params = ('', []) if scope.statewide else (' AND ces.district_code = ANY(%s)', [list(scope.codes)])
    if before:
        where += ' AND a.log_id < %s'
        params.append(before)
    with _Db() as db:
        rows = db.all(f"""
            SELECT a.log_id AS id, a.action_type AS action, a.description, a.candidate_id, a.user_email AS "by",
                   a.created_at AS at, c.first_name, c.last_name, ces.district_code AS district, ces.status
            FROM activity_log a
            JOIN candidates c ON c.candidate_id = a.candidate_id AND c.party = 'R'
            {'LEFT JOIN' if scope.statewide else 'JOIN'} candidate_election_status ces
                 ON ces.candidate_id = a.candidate_id AND ces.election_year = %s
            WHERE a.action_type = ANY(%s){where}
            ORDER BY a.created_at DESC, a.log_id DESC LIMIT %s""",
                      [CYCLE, list(RECRUIT_ACTIONS)] + params + [limit])
    items = [{'id': r['id'], 'action': r['action'], 'description': r['description'],
              'candidate_id': r['candidate_id'], 'name': f"{r['first_name'] or ''} {r['last_name'] or ''}".strip(),
              'district': r['district'], 'county': county_of_code(r['district']), 'status': r['status'],
              'by': r['by'], 'at': _json_value(r['at'])} for r in rows]
    return jsonify({'cycle': CYCLE, 'scope': scope.json(), 'items': items,
                    'next_before': items[-1]['id'] if len(items) == limit else None})


@party_api_bp.route('/search', methods=['GET'])
@require_party_key
def search():
    """Republican candidates by name (any year), with this cycle's status and district, else their latest."""
    q = re.sub(r'\s+', ' ', (request.args.get('q') or '')).strip()
    scope = _request_scope()
    if len(q) < 2:
        return jsonify({'cycle': CYCLE, 'scope': scope.json(), 'results': []})
    words = q.split(' ')[:4]
    conds, params = [], []
    for w in words:
        conds.append("(c.first_name ILIKE %s OR c.last_name ILIKE %s)")
        params += [w.replace('%', '') + '%'] * 2
    with _Db() as db:
        rows = db.all(f"""
            SELECT c.candidate_id AS id, c.first_name, c.last_name, c.city AS town,
                   cur.status, cur.district_code AS cycle_district,
                   lat.district_code AS last_district, lat.election_year AS last_year, lat.status AS last_status
            FROM candidates c
            LEFT JOIN candidate_election_status cur ON cur.candidate_id = c.candidate_id AND cur.election_year = %s
            LEFT JOIN LATERAL (SELECT district_code, election_year, status FROM candidate_election_status s
                               WHERE s.candidate_id = c.candidate_id AND s.district_code IS NOT NULL
                               ORDER BY election_year DESC, status_id DESC LIMIT 1) lat ON TRUE
            WHERE c.party = 'R' AND {' AND '.join(conds)}
            ORDER BY (cur.status_id IS NULL), c.last_name, c.first_name, c.candidate_id
            LIMIT 60""", [CYCLE] + params)
    out = []
    for r in rows:
        district = r['cycle_district'] or r['last_district']
        if not scope.allows(district):
            continue
        out.append({'id': r['id'], 'name': f"{r['first_name'] or ''} {r['last_name'] or ''}".strip(),
                    'town': r['town'], 'district': district, 'county': county_of_code(district),
                    'status': r['status'], 'in_cycle': r['status'] is not None,
                    'last_year': r['last_year'], 'last_status': r['last_status']})
    return jsonify({'cycle': CYCLE, 'scope': scope.json(), 'results': out[:25]})


# ---------------------------------------------------------------- the party app's candidate directory

@party_api_bp.route('/confirmed', methods=['GET'])
@require_party_key
def confirmed():
    """Confirmed Republicans for the cycle, the rows the party app's Candidates directory is built from (it adds
    campaign-website content itself). Campaign email and phone only; no address, voter id or comments."""
    year = request.args.get('cycle', CYCLE, type=int)
    with _Db() as db:
        rows = db.all("""
            SELECT c.candidate_id, c.first_name, c.last_name, c.party, c.incumbent, c.photo_url, c.email, c.email1,
                   c.phone1, s.district_code
            FROM candidate_election_status s JOIN candidates c ON c.candidate_id = s.candidate_id
            WHERE s.election_year = %s AND s.status = 'Confirmed'
              AND upper(coalesce(c.party, '')) IN ('R', 'REP', 'REPUBLICAN')
            ORDER BY c.last_name, c.first_name, c.candidate_id""", (year,))
    return jsonify({'cycle': year, 'count': len(rows), 'candidates': rows})


@party_api_bp.route('/ping', methods=['GET'])
@require_party_key
def ping():
    return jsonify({'ok': True, 'cycle': CYCLE})
