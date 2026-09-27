"""Reconcile 2026 filings against the SoS general election ballot (sos_general_ballot).

Chris, 27 Sep 2026: filings should match the SoS general list. A nominee we do not have gets a
new filing: 'won' (primary won by write-in) if they drew >= 35 write-in votes in that party's
primary, otherwise 'appointed'. Dry run by default; --apply writes, after snapshotting filings.
"""
import os, re, sys, sqlite3, json, collections
import psycopg2
from dotenv import load_dotenv
load_dotenv('/opt/nh-candidate-recruitment/.env')
APPLY = '--apply' in sys.argv
YEAR = 2026
SUF = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}
def toks(n): return [t for t in re.sub(r'[^a-z ]', ' ', (n or '').lower()).split() if t not in SUF]
def sur(n): t = toks(n); return t[-1] if t else ''
def first(n): t = toks(n); return t[0] if t else ''

pg = psycopg2.connect(os.environ['DATABASE_URL']); cur = pg.cursor()
cur.execute("SELECT DISTINCT upper(town), county_name FROM districts")
town_county = dict(cur.fetchall())
cur.execute("""SELECT filing_id, office, district_code, first_name, last_name, party, town, result, candidate_id
                 FROM filings WHERE election_year=%s AND office <> 'Delegate to the State Convention'""", (YEAR,))
F = [dict(zip(('id','office','district','first','last','party','town','result','cid'), r)) for r in cur.fetchall()]
cur.execute("SELECT id, office, district, county, name, sos_party, party, town FROM sos_general_ballot WHERE election_year=%s", (YEAR,))
G = [dict(zip(('id','office','district','county','name','sos_party','party','town'), r)) for r in cur.fetchall()]

def fdist(f):
    d = f['district'] or ''
    m = re.search(r'(\d+)$', d)
    if f['office'] == 'State Representative': return d
    return m.group(1) if m else None
def fcounty(f): return town_county.get((f['town'] or '').upper())
def same_seat(g, f):
    if g['office'] != f['office']: return False
    if g['office'] == 'State Representative': return g['district'] == f['district']
    if g['county']:
        c = fcounty(f)
        if c and c != g['county']: return False
    if g['district']: return fdist(f) == g['district']
    return True

# write-in votes by party primary
sq = sqlite3.connect('/opt/nh-election-results/nh_elections.db')
OFF = {'County Attorney': 9, 'County Commissioner': 13, 'Sheriff': 8, 'County Treasurer': 10, 'Executive Councilor': 5,
       'Governor': 4, 'Register of Deeds': 11, 'Register of Probate': 12, 'Representative in Congress': 3,
       'State Representative': 7, 'State Senator': 6, 'United States Senator': 2}
def writein_votes(g):
    el = {'R': 29, 'D': 30}.get(g['party'])
    if not el: return None
    rows = sq.execute("""SELECT r.district, r.county, c.name, sum(res.votes) FROM results res
                           JOIN candidates c ON c.id=res.candidate_id JOIN races r ON r.id=res.race_id
                          WHERE r.election_id=? AND r.office_id=? GROUP BY 1,2,3""", (el, OFF[g['office']])).fetchall()
    tot = 0
    for dist, county, name, v in rows:
        if g['office'] == 'State Representative' and dist != g['district']: continue
        if g['office'] != 'State Representative' and g['district'] and (dist or '') != g['district']: continue
        if g['county'] and county and county.lower() != g['county'].lower(): continue
        if sur(name) != sur(g['name']): continue
        fi = first(name)
        if fi and len(toks(name)) > 1 and not (fi[0] == first(g['name'])[:1]): continue
        tot += v
    return tot

out = collections.defaultdict(list)
matched_f = set()
for g in G:
    cands = [f for f in F if same_seat(g, f) and sur(f['last']) == sur(g['name'])]
    same = [f for f in cands if f['party'] == g['party']]
    if len(same) > 1:
        same = [f for f in same if first(f['first'])[:1] == first(g['name'])[:1]] or same
    if same:
        f = same[0]; matched_f.add(f['id'])
        if g['party'] == 'I':
            out['independent_ok'].append((g, f))
        elif f['result'] == 'won':
            out['ok'].append((g, f))
        elif f['result'] == 'pending':
            out['pending_to_won'].append((g, f))
        else:
            out['on_ballot_but_%s' % f['result']].append((g, f, writein_votes(g)))
    else:
        other = cands[0] if cands else None
        if g['party'] == 'I':
            out['independent_missing'].append((g, other))
        else:
            v = writein_votes(g)
            out['new_writein_won' if (v or 0) >= 35 else 'new_appointed'].append((g, other, v))
for f in F:
    if f['id'] not in matched_f and f['result'] in ('won', 'pending'):
        out['filing_not_on_ballot'].append(f)

for k in sorted(out): print(k, len(out[k]))
def show(k, fmt):
    print('\n==', k)
    for x in out[k]: print('  ', fmt(x))
show('pending_to_won', lambda x: f"{x[0]['office']} {x[0]['district'] or x[0]['county'] or ''} | {x[0]['name']} ({x[0]['sos_party']})")
for k in [k for k in out if k.startswith('on_ballot_but')]:
    show(k, lambda x: f"{x[0]['office']} {x[0]['district'] or ''} {x[0]['county'] or ''} | {x[0]['name']} ({x[0]['sos_party']}) filing#{x[1]['id']} write-ins {x[2]}")
for k in ('new_writein_won', 'new_appointed'):
    show(k, lambda x: f"{x[0]['office']} {x[0]['district'] or ''} {x[0]['county'] or ''} | {x[0]['name']} ({x[0]['sos_party']}) write-ins {x[2]} | other-party filing: {(x[1]['first']+' '+x[1]['last']+' '+x[1]['party']) if x[1] else '-'}")
show('independent_missing', lambda x: f"{x[0]['office']} {x[0]['district'] or ''} {x[0]['county'] or ''} | {x[0]['name']} ({x[0]['sos_party']})")
show('filing_not_on_ballot', lambda f: f"#{f['id']} {f['office']} {f['district'] or ''} | {f['first']} {f['last']} ({f['party']}) {f['result']} {f['town']}")
json.dump({k: len(v) for k, v in out.items()}, open('/root/reconcile_counts.json', 'w'))

# ------------------------------------------------------------------ apply
if not APPLY:
    sys.exit(0)
SRC = 'NH SoS general election candidate list 09/17/2026'
WHO = 'claude-sos-reconcile'
cur.execute("CREATE TABLE IF NOT EXISTS filings_bak_20260927 AS SELECT * FROM filings")
cur.execute("ALTER TABLE filings DROP CONSTRAINT IF EXISTS filings_result_check")
cur.execute("""ALTER TABLE filings ADD CONSTRAINT filings_result_check
               CHECK (result IN ('pending', 'won', 'lost', 'withdrawn', 'appointed'))""")

def setres(fid, result, note, source='sos-general', votes=None):
    cur.execute("""UPDATE filings SET result=%s, result_source=%s,
                          result_note=concat_ws('; ', nullif(result_note, ''), %s),
                          result_votes=coalesce(%s, result_votes),
                          result_recorded_at=now(), modified_by=%s, modified_at=now()
                    WHERE filing_id=%s""", (result, source, note, votes, WHO, fid))

def fcode(g):
    o, d = g['office'], g['district']
    if o == 'State Representative': return d
    if not d: return None
    return {'State Senator': f'State Senator District {d}', 'Executive Councilor': f'Executive Councilor District {d}',
            'Representative in Congress': f'NH-{d}', 'County Commissioner': f'County Comm District {d}'}.get(o)

def split_name(n):
    m = re.match(r'^(.*?),?\s+(Jr|Sr|II|III|IV)\.?$', n.strip())
    suffix = ''
    if m: n, suffix = m.group(1), ' ' + m.group(2)
    parts = n.split()
    return ' '.join(parts[:-1]), parts[-1] + suffix

def insert(g, other, result, rsource, votes, note, method):
    first_n, last_n = split_name(g['name'])
    town = (g['town'] or (other or {}).get('town') or '').upper() or None
    cur.execute("""INSERT INTO filings (election_year, office, district_code, first_name, last_name, party, town,
                                        candidate_id, filing_method, notes, source, created_by, modified_by,
                                        result, result_votes, result_recorded_at, result_source, result_note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s) RETURNING filing_id""",
                (YEAR, g['office'], fcode(g), first_n, last_n, g['party'], town, (other or {}).get('cid'),
                 method, None, SRC, WHO, WHO, result, votes, rsource, note))
    return cur.fetchone()[0]

log = collections.Counter()
for g, f in out['pending_to_won']:
    setres(f['id'], 'won', 'on SoS general ballot'); log['pending->won'] += 1
for g, f, v in out['on_ballot_but_lost']:
    if sur(g['name']) == 'drew':
        setres(f['id'], 'won', 'recount: on SoS general ballot'); log['Drew lost->won'] += 1
    elif sur(g['name']) == 'gorman':
        setres(f['id'], 'appointed', 'Cote withdrew; appointed to the R line per SoS general ballot'); log['Gorman R lost->appointed'] += 1
for g, other, v in out['new_writein_won']:
    insert(g, other, 'won', 'sos-writein', v, f'won {g["sos_party"]} primary by write-in ({v} votes); on SoS general ballot', 'write-in')
    log['new write-in nominee'] += 1
for g, other, v in out['new_appointed']:
    insert(g, other, 'appointed', 'sos-general', v, 'appointed; on SoS general ballot', None)
    log['new appointed'] += 1
for g, other in out['independent_missing']:
    insert(g, other, 'pending', 'sos-general', None, f'nomination papers ({g["sos_party"]}); on SoS general ballot', None)
    log['new independent'] += 1
for f in out['filing_not_on_ballot']:
    if f['last'] == 'Leavitt':
        setres(f['id'], 'lost', 'recount: Drew is on the SoS general ballot'); log['Leavitt won->lost'] += 1
    elif f['last'] == 'Cote':
        setres(f['id'], 'withdrawn', 'not on SoS general ballot; Gorman appointed to the R line'); log['Cote won->withdrawn'] += 1
    elif f['party'] == 'I':
        setres(f['id'], 'lost', 'not on SoS general ballot (nomination papers)'); log['independent not on ballot'] += 1
    else:
        print('UNHANDLED', f)
pg.commit()
print(dict(log))
