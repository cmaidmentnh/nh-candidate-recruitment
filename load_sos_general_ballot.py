"""Load the Secretary of State's 2026 general election candidate list into sos_general_ballot.

Sources (public record, committed under data/sos_general_2026/):
  ge_candidates.txt   district|name|party|town            State Representative, one line each
  ge_offices.txt      office|district|name|party|county    every other office

Full replace on every run. The SoS list does not give a town for non-House offices, so those
are filled from our own filings when exactly one filing matches office, party and surname.

Usage: venv/bin/python load_sos_general_ballot.py [--dry-run]
"""
import os
import re
import sys

import psycopg2
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data', 'sos_general_2026')
YEAR = 2026
SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}


def party_letter(p):
    return {'DEM': 'D', 'REP': 'R'}.get(p, 'I')


def surname(name):
    toks = [t for t in re.sub(r'[^a-z ]', ' ', name.lower()).split() if t not in SUFFIXES]
    return toks[-1] if toks else ''


def title(s):
    return ' '.join(w.capitalize() for w in s.split()) if s else None


def read_rows():
    rows = []
    with open(os.path.join(DATA, 'ge_candidates.txt')) as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            district, name, party, town = [x.strip() for x in line.rstrip('\n').split('|')]
            rows.append(dict(office='State Representative', district=district,
                             county=district.rsplit(' ', 1)[0], name=name, sos_party=party,
                             party=party_letter(party), town=title(town), source_line=n))
    with open(os.path.join(DATA, 'ge_offices.txt')) as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            office, district, name, party, county = [x.strip() for x in line.rstrip('\n').split('|')]
            rows.append(dict(office=office, district=district or None, county=title(county),
                             name=name, sos_party=party, party=party_letter(party), town=None,
                             source_line=n))
    return rows


def fill_towns(cur, rows):
    cur.execute("""SELECT office, party, last_name, town FROM filings
                    WHERE election_year = %s AND result <> 'lost'""", (YEAR,))
    idx = {}
    for office, party, last, town in cur.fetchall():
        idx.setdefault((office, party, surname(last or '')), set()).add(town)
    filled = 0
    for r in rows:
        if r['town']:
            continue
        towns = idx.get((r['office'], r['party'], surname(r['name'])), set())
        if len(towns) == 1:
            r['town'] = title(next(iter(towns)))
            filled += 1
    return filled


def main():
    load_dotenv(os.path.join(HERE, '.env'))
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    rows = read_rows()
    filled = fill_towns(cur, rows)
    no_town = sum(1 for r in rows if not r['town'])
    print(f'{len(rows)} ballot lines; towns filled from filings for {filled}; {no_town} without a town')
    if '--dry-run' in sys.argv:
        return
    cur.execute('DELETE FROM sos_general_ballot WHERE election_year = %s', (YEAR,))
    for r in rows:
        cur.execute("""INSERT INTO sos_general_ballot
                         (election_year, office, district, county, name, sos_party, party, town, source_line)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (YEAR, r['office'], r['district'], r['county'], r['name'], r['sos_party'],
                     r['party'], r['town'], r['source_line']))
    conn.commit()
    print('loaded')


if __name__ == '__main__':
    main()
