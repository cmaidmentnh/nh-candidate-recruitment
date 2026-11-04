"""The sitting members, read from the results site.

elections.nhhouse.gop is where the House results live and where recounts and certification
get entered. This copies its State House winners into `incumbents` for the PRIOR general so
the recruitment pages know who holds each seat, and rewrites them every 10 minutes so a
called race, a flipped recount or a certification shows up here without anyone re-entering
it. Once every race on the board is certified the rows are marked certified and the sync
stops touching them.

Winners are matched to our own records through that year's filings (district + last name,
then first name), so a winner carries the candidate_id the rest of the app keys on. A
winner we have no filing for (a write-in) is stored by name with no candidate_id.

Guard: the results site serves whichever general it is configured for. If the names on its
board stop matching our filings for PRIOR (it has moved on to the next cycle), the sync
refuses to write rather than replace the sitting members with someone else's race.
"""
import json
import logging
import re
import threading
import time
import urllib.request
from collections import defaultdict

from cycle import PRIOR

logger = logging.getLogger(__name__)

get_db_connection = None
release_db_connection = None

BOARD_URL = 'https://elections.nhhouse.gop/api/contested/state-house/board?event=general&scope=all'
SYNC_EVERY_S = 600
SYNC_LOCK = 7_420_281   # pg advisory lock id: one worker syncs at a time
MIN_MATCH = 0.8         # share of winners that must match our filings before we write
WON_STATUSES = ('called', 'unopposed')
_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv'}
_thread = None


def init_incumbents_sync(db_conn_func, db_release_func):
    global get_db_connection, release_db_connection
    get_db_connection = db_conn_func
    release_db_connection = db_release_func


def _tokens(name):
    t = [w for w in re.sub(r"[^a-z' ]", ' ', (name or '').lower()).split() if w not in _SUFFIXES]
    return [w for w in t if len(w) > 1] or t


def _match(winner, pool):
    """The one record in this district's pool that is this winner, or None."""
    t = _tokens(winner)
    if not t:
        return None
    hits = [p for p in pool if _tokens(p['last'])[-1:] == t[-1:]]
    for narrow in (lambda p: _tokens(p['first'])[:1] == t[:1],
                   lambda p: (_tokens(p['first'])[:1] or [''])[0][:1] == t[0][:1],
                   lambda p: p['cid'] is not None):
        if len(hits) > 1:
            hits = [p for p in hits if narrow(p)] or hits
    if not hits:
        return None
    # Duplicate filings for one person share a candidate_id; prefer the one that has it.
    return next((p for p in hits if p['cid']), hits[0])


def fetch_board():
    req = urllib.request.Request(BOARD_URL, headers={'User-Agent': 'nh-candidate-recruitment'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)['races']


def build_rows(races, pool_by_district):
    """(rows, winners_seen, winners_matched, all_certified) from the board."""
    rows, seen, matched = [], 0, 0
    all_certified = bool(races)
    for r in races:
        code = f"{r['county']} {r['district']}".strip()
        pj = r.get('projection') or {}
        certified = bool(pj.get('certified'))
        all_certified = all_certified and certified
        called = certified or pj.get('status') in WON_STATUSES
        seats = r.get('seats') or 1
        party_of = {c['name']: c.get('party') or 'O' for c in r['candidates']}
        # Same rule as the results site's seat tally: with 3 seats and 3 D, 1 R on the
        # ballot, 2 seats are D's whatever the count says, so they are won, not leading.
        named = [c for c in r['candidates'] if c['name'] != 'Write-in']
        per = defaultdict(int)
        for c in named:
            per[c.get('party') or 'O'] += 1
        locked = {k: min(n, max(0, seats - (len(named) - n))) for k, n in per.items()}
        for w in (pj.get('winners') or [])[:seats]:
            if w == 'Write-in':
                continue
            k = party_of.get(w) or 'O'
            if called:
                status = 'won'
            elif locked.get(k):
                locked[k] -= 1
                status = 'won'
            else:
                status = 'leading'
            seen += 1
            hit = _match(w, pool_by_district.get(code, []))
            if hit:
                matched += 1
            rows.append((PRIOR, code, w, k[:1], status, certified,
                         hit['cid'] if hit else None))
    return rows, seen, matched, all_certified


def _pool(cur):
    """PRIOR's State Rep nominees by district, from filings, with a candidate_id where known."""
    cur.execute("""SELECT district_code, first_name, last_name, candidate_id FROM filings
                    WHERE election_year = %s AND office = 'State Representative'
                      AND result NOT IN ('lost', 'withdrawn')""", (PRIOR,))
    pool = defaultdict(list)
    for d, f, l, cid in cur.fetchall():
        pool[d].append({'first': f, 'last': l, 'cid': cid})
    return pool


def sync(force=False):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK,))
        if not cur.fetchone()[0]:
            return {'skipped': 'locked'}
        try:
            cur.execute("""SELECT count(*), count(*) FILTER (WHERE certified) FROM incumbents
                            WHERE election_year = %s AND source = 'results'""", (PRIOR,))
            have, have_cert = cur.fetchone()
            if have and have == have_cert and not force:
                return {'skipped': 'certified'}
            races = fetch_board()
            rows, seen, matched, all_cert = build_rows(races, _pool(cur))
            if not seen or matched / seen < MIN_MATCH:
                logger.warning('incumbents sync: %s of %s winners match %s filings; not writing',
                               matched, seen, PRIOR)
                return {'skipped': 'mismatch', 'seen': seen, 'matched': matched}
            cur.execute("DELETE FROM incumbents WHERE election_year = %s AND source = 'results'",
                        (PRIOR,))
            cur.executemany("""INSERT INTO incumbents (election_year, district_code, name, party,
                                   status, certified, candidate_id, source)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, 'results')
                               ON CONFLICT DO NOTHING""", rows)
            conn.commit()
            return {'written': len(rows), 'matched': matched, 'certified': all_cert}
        finally:
            conn.rollback()
            cur.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK,))
            conn.commit()
            cur.close()
    finally:
        release_db_connection(conn)


def _loop():
    time.sleep(90)
    while True:
        try:
            res = sync()
            if res.get('written'):
                logger.info('incumbents sync: %s', res)
        except Exception:
            logger.exception('incumbents sync failed')
        time.sleep(SYNC_EVERY_S)


def start_auto_sync():
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_loop, name='incumbents-sync', daemon=True)
        _thread.start()


def register_cli(app):
    @app.cli.command('sync-incumbents')
    def _cli():
        """Re-read the PRIOR general's House winners from the results site now."""
        print(sync(force=True))
