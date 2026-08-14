"""
Candidate cross-reference API (/api/v1/*).

Lets an outside organisation check its own membership list against the 2026 NH
candidate filings without us handing over any contact information. The only
fields that ever leave this module are name, town, party, office and district,
all of which are public record from the filing itself.

Deliberately NOT exposed: email, phone, address, zip, Signal, social handles,
recruitment status. In particular the recruitment pipeline (Confirmed /
Declined / Considering in candidate_election_status) is never read here, because
"who we asked and who said no" is not public and not ours to leak. This module
reads `filings` only, which is the record of an actual filed candidacy.

Auth is an API key sent as `X-API-Key` or `Authorization: Bearer <key>`. Keys are
stored as SHA-256 hashes; issue them with `flask issue-match-key`.
"""
import hashlib
import re
import secrets
import time
import unicodedata
from functools import wraps

from flask import Blueprint, request, jsonify, current_app

match_api_bp = Blueprint('match_api', __name__)

_get_db = None
_release_db = None

ELECTION_YEAR = 2026
MAX_BATCH = 5000
CACHE_TTL = 300

# Common formal/nickname pairs seen on NH filings. Matching is bidirectional and
# transitive through the formal name, so "Bill" matches a filed "William".
NICKNAMES = {
    'william': {'bill', 'billy', 'will', 'willie'},
    'robert': {'bob', 'bobby', 'rob', 'robbie'},
    'richard': {'dick', 'rick', 'rich', 'ricky'},
    'james': {'jim', 'jimmy', 'jamie'},
    'john': {'jack', 'johnny', 'jon'},
    'joseph': {'joe', 'joey'},
    'charles': {'charlie', 'chuck', 'chas'},
    'thomas': {'tom', 'tommy'},
    'michael': {'mike', 'mickey'},
    'edward': {'ed', 'eddie', 'ted', 'teddy'},
    'anthony': {'tony'},
    'daniel': {'dan', 'danny'},
    'david': {'dave', 'davey'},
    'donald': {'don', 'donnie'},
    'kenneth': {'ken', 'kenny'},
    'lawrence': {'larry'},
    'matthew': {'matt'},
    'nicholas': {'nick'},
    'peter': {'pete'},
    'ronald': {'ron', 'ronnie'},
    'stephen': {'steve', 'steven'},
    'steven': {'steve', 'stephen'},
    'timothy': {'tim', 'timmy'},
    'gerald': {'gerry', 'jerry'},
    'francis': {'frank', 'fran'},
    'frederick': {'fred', 'freddie'},
    'gregory': {'greg'},
    'benjamin': {'ben', 'benny'},
    'samuel': {'sam', 'sammy'},
    'alexander': {'alex', 'al'},
    'andrew': {'andy', 'drew'},
    'christopher': {'chris'},
    'patrick': {'pat', 'paddy'},
    'raymond': {'ray'},
    'walter': {'walt'},
    'eugene': {'gene'},
    'howard': {'howie'},
    'douglas': {'doug'},
    'philip': {'phil'},
    'phillip': {'phil'},
    'russell': {'russ'},
    'vincent': {'vinny', 'vince'},
    'elizabeth': {'liz', 'beth', 'betty', 'lisa', 'eliza'},
    'katherine': {'kathy', 'kate', 'katie', 'kay'},
    'kathleen': {'kathy', 'kate', 'katie'},
    'margaret': {'peggy', 'maggie', 'meg', 'marge'},
    'patricia': {'pat', 'patty', 'trish', 'tricia'},
    'deborah': {'deb', 'debbie'},
    'barbara': {'barb', 'babs'},
    'jennifer': {'jen', 'jenny'},
    'jacqueline': {'jackie'},
    'christine': {'chris', 'christy', 'tina'},
    'christina': {'chris', 'christy', 'tina'},
    'susan': {'sue', 'susie'},
    'sandra': {'sandy'},
    'cynthia': {'cindy', 'cinde'},
    'theresa': {'terry', 'teri', 'tess'},
    'teresa': {'terry', 'teri', 'tess'},
    'rebecca': {'becky', 'becca'},
    'victoria': {'vicki', 'vicky'},
    'virginia': {'ginny', 'ginger'},
    'pamela': {'pam'},
    'diane': {'di'},
    'judith': {'judy'},
    'janet': {'jan'},
    'joanne': {'jo'},
    'marjorie': {'marge', 'margie'},
    'eleanor': {'ellie', 'nell'},
    'dorothy': {'dot', 'dottie'},
    'constance': {'connie'},
    'gertrude': {'gertie'},
    'suzanne': {'sue', 'suzy'},
    'roberta': {'bobbi'},
    'antoinette': {'toni'},
    'veronica': {'ronnie'},
    'nathaniel': {'nate'},
    'zachary': {'zach'},
    'jeffrey': {'jeff'},
    'geoffrey': {'geoff', 'jeff'},
    'bradley': {'brad'},
    'terrance': {'terry'},
    'leonard': {'len', 'lenny'},
    'albert': {'al', 'bert'},
    'alfred': {'al', 'fred'},
    'herbert': {'herb'},
    'norman': {'norm'},
    'stanley': {'stan'},
    'sylvester': {'sly'},
    'wesley': {'wes'},
}

# flattened alias -> set of canonical forms, built once
_ALIAS = {}
for _formal, _nicks in NICKNAMES.items():
    _ALIAS.setdefault(_formal, set()).add(_formal)
    for _n in _nicks:
        _ALIAS.setdefault(_n, set()).add(_formal)
        _ALIAS.setdefault(_formal, set()).add(_n)

_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v', 'md', 'phd', 'esq'}

_cache = {'ts': 0.0, 'rows': None, 'index': None}


def init_match_api(get_db_connection, release_db_connection):
    global _get_db, _release_db
    _get_db = get_db_connection
    _release_db = release_db_connection


# ---------------------------------------------------------------- normalisation

def _strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '')
                   if not unicodedata.combining(c))


def _norm(s):
    """Lowercase, de-accent, drop anything that is not a letter or space."""
    s = _strip_accents(s).lower()
    return re.sub(r'\s+', ' ', re.sub(r"[^a-z ]", ' ', s)).strip()


def _norm_last(s):
    """Surname without generational suffixes, so 'Smith Jr' keys as 'smith'."""
    parts = [p for p in _norm(s).split() if p not in _SUFFIXES]
    return ' '.join(parts)


def _norm_first(s):
    """First token only, so 'Mary Jo' and 'Mary' collide deliberately."""
    parts = _norm(s).split()
    return parts[0] if parts else ''


def _variants(first):
    """All forms a given first name could legitimately appear under."""
    if not first:
        return set()
    out = {first}
    out |= _ALIAS.get(first, set())
    return out


def _pretty_town(s):
    """Filings store towns uppercase ('NEW BOSTON'); present them readably.
    Matching always runs off _norm(), so this is cosmetic only."""
    if not s:
        return s
    return ' '.join(w.capitalize() for w in s.split())


# ---------------------------------------------------------------- roster + index

ROSTER_SQL = """
    SELECT f.candidate_id,
           f.filing_id,
           f.first_name,
           f.last_name,
           f.town,
           f.party,
           f.office,
           f.district_code
      FROM filings f
     WHERE f.election_year = %s
     ORDER BY f.last_name, f.first_name
"""


def _load_roster():
    """Public-safe projection of the 2026 filings. Explicit column list is the
    enforcement point for 'no contact information' — do not switch to SELECT *."""
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute(ROSTER_SQL, (ELECTION_YEAR,))
        rows = []
        for r in cur.fetchall():
            rows.append({
                'id': r[0],
                'filing_id': r[1],
                'first_name': r[2],
                'last_name': r[3],
                'town': _pretty_town(r[4]),
                'party': r[5],
                'office': r[6],
                'district': r[7],
                'year': ELECTION_YEAR,
            })
        cur.close()
        return rows
    finally:
        _release_db(conn)


def _build_index(rows):
    """last-name -> [record], plus precomputed name keys per record."""
    idx = {}
    for r in rows:
        r['_last'] = _norm_last(r['last_name'])
        r['_first'] = _norm_first(r['first_name'])
        r['_firstvars'] = _variants(r['_first'])
        r['_town'] = _norm(r['town'])
        idx.setdefault(r['_last'], []).append(r)
    return idx


def _roster():
    now = time.time()
    if _cache['rows'] is None or (now - _cache['ts']) > CACHE_TTL:
        rows = _load_roster()
        _cache['rows'] = rows
        _cache['index'] = _build_index(rows)
        _cache['ts'] = now
    return _cache['rows'], _cache['index']


def _public(r):
    """Strip internal keys before serialising."""
    return {k: v for k, v in r.items() if not k.startswith('_') and k != 'filing_id'}


# ---------------------------------------------------------------- matching

def _match_one(q_first, q_last, q_town, index):
    """Return (matches, best_confidence). Never raises on odd input."""
    last = _norm_last(q_last)
    if not last:
        return [], None
    cands = index.get(last, [])
    if not cands:
        return [], None

    first = _norm_first(q_first)
    town = _norm(q_town)
    fvars = _variants(first)

    out = []
    for r in cands:
        if first and r['_first']:
            if first == r['_first']:
                kind, conf = 'exact', 0.95
            elif fvars & r['_firstvars']:
                kind, conf = 'nickname', 0.85
            elif len(first) == 1 and r['_first'].startswith(first):
                kind, conf = 'initial', 0.60
            elif len(r['_first']) == 1 and first.startswith(r['_first']):
                kind, conf = 'initial', 0.60
            elif r['_first'].startswith(first) or first.startswith(r['_first']):
                kind, conf = 'partial', 0.55
            else:
                continue
        else:
            kind, conf = 'surname_only', 0.30

        if town and r['_town']:
            if town == r['_town']:
                conf = min(0.99, conf + 0.05)
                kind = kind + '+town'
            else:
                # a supplied town that disagrees is real evidence against
                conf = max(0.05, conf - 0.35)

        m = _public(r)
        m['match_type'] = kind
        m['confidence'] = round(conf, 2)
        out.append(m)

    out.sort(key=lambda m: -m['confidence'])
    return out, (out[0]['confidence'] if out else None)


# ---------------------------------------------------------------- auth

def _hash_key(k):
    return hashlib.sha256(k.encode('utf-8')).hexdigest()


def _present_key():
    k = request.headers.get('X-API-Key', '').strip()
    if k:
        return k
    auth = request.headers.get('Authorization', '').strip()
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return ''


def require_api_key(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        key = _present_key()
        if not key:
            return jsonify({'error': 'missing_api_key',
                            'detail': 'Send X-API-Key or Authorization: Bearer <key>.'}), 401
        conn = _get_db()
        try:
            cur = conn.cursor()
            cur.execute("""SELECT key_id, org_name FROM match_api_keys
                            WHERE key_hash = %s AND active = true""", (_hash_key(key),))
            row = cur.fetchone()
            if not row:
                cur.close()
                return jsonify({'error': 'invalid_api_key'}), 401
            request.api_key_id, request.api_org = row[0], row[1]
            cur.close()
        finally:
            _release_db(conn)
        return fn(*a, **kw)
    return wrapper


def _record_usage(key_id, matched):
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute("""UPDATE match_api_keys
                          SET call_count = call_count + 1,
                              rows_matched = rows_matched + %s,
                              last_used_at = now()
                        WHERE key_id = %s""", (int(matched), key_id))
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
    finally:
        _release_db(conn)


# ---------------------------------------------------------------- routes

@match_api_bp.route('/api/v1/candidates', methods=['GET'])
@require_api_key
def list_candidates():
    """Full 2026 filed-candidate roster, for callers who prefer to match locally."""
    rows, _ = _roster()
    party = (request.args.get('party') or '').strip().upper()
    office = (request.args.get('office') or '').strip().lower()
    town = _norm(request.args.get('town') or '')

    out = []
    for r in rows:
        if party and (r['party'] or '').upper() != party:
            continue
        if office and office not in (r['office'] or '').lower():
            continue
        if town and _norm(r['town']) != town:
            continue
        out.append(_public(r))

    return jsonify({'year': ELECTION_YEAR, 'count': len(out), 'candidates': out})


@match_api_bp.route('/api/v1/match', methods=['POST'])
@require_api_key
def match_members():
    """Cross-reference a membership list against 2026 filed candidates.

    Body: {"records": [{"id": "...", "first_name": "...", "last_name": "...",
                        "town": "..."} , ...],
           "min_confidence": 0.6, "matched_only": true}

    We return the caller's own `id` back so they can join to their database
    without us ever storing it.
    """
    body = request.get_json(silent=True) or {}
    records = body.get('records')
    if not isinstance(records, list):
        return jsonify({'error': 'bad_request',
                        'detail': '"records" must be a list.'}), 400
    if len(records) > MAX_BATCH:
        return jsonify({'error': 'batch_too_large',
                        'detail': f'Maximum {MAX_BATCH} records per request.'}), 413

    try:
        min_conf = float(body.get('min_confidence', 0.6))
    except (TypeError, ValueError):
        min_conf = 0.6
    matched_only = bool(body.get('matched_only', True))

    _, index = _roster()
    results = []
    n_matched = 0

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        first = rec.get('first_name') or ''
        last = rec.get('last_name') or ''
        town = rec.get('town') or rec.get('city') or ''
        if not last and rec.get('name'):
            # tolerate a single "name" field, "Last, First" or "First Last"
            nm = str(rec['name'])
            if ',' in nm:
                last, _, first = nm.partition(',')
            else:
                parts = nm.split()
                first, last = (parts[0], ' '.join(parts[1:])) if len(parts) > 1 else ('', nm)

        matches, best = _match_one(first, last, town, index)
        matches = [m for m in matches if m['confidence'] >= min_conf]
        if matches:
            n_matched += 1
        elif matched_only:
            continue

        results.append({
            'input_id': rec.get('id', i),
            'matched': bool(matches),
            'best_confidence': matches[0]['confidence'] if matches else None,
            'matches': matches,
        })

    _record_usage(getattr(request, 'api_key_id', None), n_matched)

    return jsonify({
        'year': ELECTION_YEAR,
        'submitted': len(records),
        'matched': n_matched,
        'returned': len(results),
        'min_confidence': min_conf,
        'results': results,
    })


@match_api_bp.route('/api/v1/health', methods=['GET'])
def health():
    return jsonify({'ok': True, 'year': ELECTION_YEAR})


# ---------------------------------------------------------------- key issuance

def register_cli(app):
    @app.cli.command('issue-match-key')
    def issue_match_key():
        """Issue an API key. Prints the plaintext once; only the hash is stored."""
        import click
        org = click.prompt('Organisation name')
        email = click.prompt('Contact email', default='', show_default=False)
        raw = 'ctehr_' + secrets.token_urlsafe(32)
        conn = _get_db()
        try:
            cur = conn.cursor()
            cur.execute("""INSERT INTO match_api_keys
                             (key_hash, key_prefix, org_name, contact_email, created_by)
                           VALUES (%s, %s, %s, %s, %s) RETURNING key_id""",
                        (_hash_key(raw), raw[:16], org, email or None, 'cli'))
            kid = cur.fetchone()[0]
            conn.commit()
            cur.close()
        finally:
            _release_db(conn)
        click.echo(f'\nKey #{kid} for {org}\n\n    {raw}\n\n'
                   'Store it now. It cannot be recovered.')

    @app.cli.command('revoke-match-key')
    def revoke_match_key():
        import click
        kid = click.prompt('Key id to revoke', type=int)
        conn = _get_db()
        try:
            cur = conn.cursor()
            cur.execute('UPDATE match_api_keys SET active=false WHERE key_id=%s', (kid,))
            conn.commit()
            cur.close()
        finally:
            _release_db(conn)
        click.echo(f'Key #{kid} revoked.')
