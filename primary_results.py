#!/usr/bin/env python3
"""Derive 2026 primary outcomes for our filed candidates.

Reads the results SQLite (/opt/nh-election-results/nh_elections.db, read-only) and the
recruitment Postgres, and works out, per race, which of OUR filings won and which lost.

The one rule that matters: rank only candidates on the ballot roster
(race_candidates.recruitment_filing_id > 0). The `results` table also stores scattered
write-in names as candidates, including real people written in on the other party's ballot,
so ranking everything in `results` invents hundreds of losers that do not exist.

Modes:
  review        print the races where at least one of our filings would be marked lost
  json PATH     write the same structure as JSON
  apply PATH    write decisions from a JSON file back into filings.result
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

SQLITE_PATH = '/opt/nh-election-results/nh_elections.db'
ELECTIONS = {29: 'R', 30: 'D'}
# Recount eligibility, RSA 660:7: within 10 votes, or within 1.5% of ballots cast.
RECOUNT_VOTES = 10
RECOUNT_PCT = 0.015


def load_env(path='/opt/nh-candidate-recruitment/.env'):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def fetch_races():
    """Every 2026 primary race, with our roster candidates and their vote totals."""
    con = sqlite3.connect(f'file:{SQLITE_PATH}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT r.id                        AS race_id,
               r.election_id               AS election_id,
               o.name                      AS office,
               r.county                    AS county,
               r.district                  AS district,
               r.seats                     AS seats,
               rc.recruitment_filing_id    AS filing_id,
               rc.recruitment_candidate_id AS candidate_id,
               c.name                      AS name,
               COALESCE(SUM(res.votes), 0) AS votes,
               COUNT(res.id)               AS town_rows
        FROM races r
        JOIN offices  o  ON o.id = r.office_id
        JOIN race_candidates rc ON rc.race_id = r.id
        JOIN candidates c ON c.id = rc.candidate_id
        LEFT JOIN results res
               ON res.race_id = r.id AND res.candidate_id = rc.candidate_id
        WHERE r.election_id IN (29, 30)
          AND rc.recruitment_filing_id > 0
        GROUP BY r.id, rc.candidate_id
    """).fetchall()

    # Reported vs expected towns. district_compositions only carries State Representative,
    # so completeness is knowable for House races and unknown for everything else.
    reported = {r['race_id']: r['n'] for r in con.execute(
        'SELECT race_id, COUNT(DISTINCT municipality) n FROM results GROUP BY race_id')}
    expected = {}
    for r in con.execute("""
            SELECT r.id race_id, COUNT(DISTINCT dc.municipality) n
            FROM races r
            JOIN offices o ON o.id = r.office_id AND o.name = 'State Representative'
            JOIN district_compositions dc
              ON dc.office = o.name AND dc.county = r.county
             AND dc.district = r.district AND dc.redistricting_cycle = '2022-2030'
            WHERE r.election_id IN (29, 30)
            GROUP BY r.id"""):
        expected[r['race_id']] = r['n']
    con.close()

    races = defaultdict(lambda: {'candidates': []})
    for row in rows:
        race = races[row['race_id']]
        race.update(race_id=row['race_id'], party=ELECTIONS[row['election_id']],
                    office=row['office'], county=row['county'],
                    district=row['district'], seats=row['seats'])
        race['candidates'].append({
            'filing_id': row['filing_id'], 'candidate_id': row['candidate_id'],
            'name': row['name'], 'votes': row['votes'], 'town_rows': row['town_rows']})

    for race in races.values():
        race['towns_reported'] = reported.get(race['race_id'], 0)
        race['towns_expected'] = expected.get(race['race_id'])
        race['complete'] = (race['towns_expected'] is not None
                            and race['towns_reported'] >= race['towns_expected'])
        _rank(race)
    return races


def _rank(race):
    """Rank roster candidates, mark winners, and flag anything a person must look at."""
    cands = sorted(race['candidates'], key=lambda c: -c['votes'])
    seats = race['seats'] or 1

    rank, prev_votes, prev_rank = 0, None, 0
    for i, c in enumerate(cands, start=1):
        if c['votes'] == prev_votes:
            rank = prev_rank            # standard competition ranking, ties share a rank
        else:
            rank = i
        c['rank'] = rank
        prev_votes, prev_rank = c['votes'], rank

    for c in cands:
        c['computed'] = 'won' if c['rank'] <= seats else 'lost'

    winners = [c for c in cands if c['computed'] == 'won']
    losers = [c for c in cands if c['computed'] == 'lost']
    cutoff = min((c['votes'] for c in winners), default=0)
    total = sum(c['votes'] for c in cands)
    ballots = (total / seats) if seats else total     # multi-seat: each voter casts `seats` votes

    for c in losers:
        c['margin'] = cutoff - c['votes']
        c['recount_eligible'] = (c['margin'] < RECOUNT_VOTES
                                 or (ballots and c['margin'] < RECOUNT_PCT * ballots))

    race['candidates'] = cands
    race['total_votes'] = total
    race['has_losers'] = bool(losers)
    # More winners than seats means a tie straddles the cutoff. A person must break it.
    race['tie_at_cutoff'] = len(winners) > seats
    race['no_results'] = total == 0
    race['recount_watch'] = [c['name'] for c in losers if c.get('recount_eligible')]
    return race


def enrich_from_postgres(races):
    """Attach our own filing detail so the review reads in our language, not the SoS's."""
    import psycopg2
    con = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = con.cursor()
    ids = [c['filing_id'] for r in races.values() for c in r['candidates']]
    cur.execute("""
        SELECT f.filing_id, f.first_name, f.last_name, f.party, f.office,
               f.district_code, f.town, f.result, f.candidate_id
        FROM filings f WHERE f.filing_id = ANY(%s)""", (ids,))
    by_id = {r[0]: r for r in cur.fetchall()}
    con.close()

    missing = 0
    for race in races.values():
        for c in race['candidates']:
            f = by_id.get(c['filing_id'])
            if not f:
                missing += 1
                c['filing_missing'] = True
                continue
            c['filed_name'] = f'{f[1]} {f[2]}'
            c['filed_party'] = f[3]
            c['district_code'] = f[5]
            c['town'] = f[6]
            c['current_result'] = f[7]
    return missing


def review_list(races):
    """Races where at least one of our filings would be marked lost."""
    out = [r for r in races.values() if r['has_losers'] and not r['no_results']]
    return sorted(out, key=lambda r: (r['party'], r['office'], r['county'] or '',
                                      str(r['district'] or '')))


def print_review(races):
    rows = review_list(races)
    print(f'races needing a decision: {len(rows)}')
    print(f'candidates who would be marked lost: '
          f'{sum(1 for r in rows for c in r["candidates"] if c["computed"] == "lost")}')
    print()
    for r in rows:
        where = f'{r["county"] or ""} {r["district"] or ""}'.strip()
        state = ('complete' if r['complete']
                 else f'PARTIAL {r["towns_reported"]}/{r["towns_expected"]}'
                 if r['towns_expected'] else 'completeness unknown')
        flags = []
        if r['tie_at_cutoff']:
            flags.append('TIE AT CUTOFF')
        if r['recount_watch']:
            flags.append('recount: ' + ', '.join(r['recount_watch']))
        print(f'{r["party"]} {r["office"]} {where} | {r["seats"]} seat(s) | {state}'
              + ('  << ' + ' | '.join(flags) if flags else ''))
        for c in r['candidates']:
            mark = 'WON ' if c['computed'] == 'won' else 'lost'
            extra = ''
            if c['computed'] == 'lost':
                extra = f'  (-{c["margin"]})' + ('  RECOUNT' if c['recount_eligible'] else '')
            print(f'    {mark} {c["votes"]:>6}  {c.get("filed_name", c["name"])}{extra}')
        print()


def apply_decisions(path):
    """Write decisions back. Expects [{filing_id, result, votes, note}]."""
    import psycopg2
    decisions = json.load(open(path))
    con = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = con.cursor()
    n = 0
    for d in decisions:
        if d['result'] not in ('pending', 'won', 'lost', 'withdrawn'):
            raise ValueError(f'bad result {d["result"]!r} for filing {d["filing_id"]}')
        cur.execute("""
            UPDATE filings
               SET result = %s, result_votes = %s, result_recorded_at = %s,
                   result_source = %s, result_note = %s
             WHERE filing_id = %s""",
                    (d['result'], d.get('votes'), datetime.now(),
                     d.get('source', 'chris'), d.get('note'), d['filing_id']))
        n += cur.rowcount
    con.commit()
    con.close()
    print(f'updated {n} filings')


if __name__ == '__main__':
    load_env()
    mode = sys.argv[1] if len(sys.argv) > 1 else 'review'
    if mode == 'apply':
        apply_decisions(sys.argv[2])
        sys.exit(0)

    races = fetch_races()
    missing = enrich_from_postgres(races)
    if missing:
        print(f'WARNING: {missing} roster entries point at a filing_id we do not have\n')
    if mode == 'json':
        rows = review_list(races)
        json.dump(rows, open(sys.argv[2], 'w'), indent=1, default=str)
        print(f'wrote {len(rows)} races to {sys.argv[2]}')
    else:
        print_review(races)
