"""Append the undeclared-modelling columns to the SoS absentee xlsx, one row per original row."""
import csv, json, math, sys, collections
import xlsxwriter, openpyxl

run, src, out = sys.argv[1:4]

gov = collections.defaultdict(lambda: [0, 0])
for line in open(f'{run}/gov2024_town.psv'):
    m, p, v = line.rstrip('\n').split('|')
    if p == 'Republican': gov[m][0] += int(v)
    elif p == 'Democratic': gov[m][1] += int(v)
gshare = {m: r / (r + d) for m, (r, d) in gov.items() if r + d}
state = sum(r for r, d in gov.values()) / sum(r + d for r, d in gov.values())
logit = lambda p: math.log(p / (1 - p)); inv = lambda x: 1 / (1 + math.exp(-x))

def tshare(town, ward):
    t = town.strip().title()
    k = f"{t} Ward {int(ward)}" if ward and ward not in ('00', '0') else t
    return gshare.get(k) or gshare.get(t) or state

enr = json.load(open(f'{run}/absentee_enrich.json'))
S = {}
for v in csv.DictReader(open(f'{run}/absentee_scored.csv')):
    S.setdefault(v['voter_id'], v)
has = lambda v: v['idx'] not in ('', 'None')
und_hist = [v for v in S.values() if v['reg'] == 'UND' and has(v)]
shift = sum(logit(min(.97, max(.03, .5 + .4 * float(v['idx'])))) - logit(tshare(v['town'], v['ward']))
            for v in und_hist) / len(und_hist)

def model(v):
    ts = tshare(v['town'], v['ward'])
    if v['reg'] == 'REP': return 0.93, 'Registered Republican', ts
    if v['reg'] == 'DEM': return 0.05, 'Registered Democrat', ts
    if has(v): return .5 + .4 * float(v['idx']), 'Undeclared: primary ballot history', ts
    return inv(logit(ts) + shift), 'Undeclared: town 2024 result, adjusted', ts

wb = openpyxl.load_workbook(src, read_only=True)
ws = wb.active
rows = ws.iter_rows(values_only=True)
header = list(next(rows))
idcol = header.index('Voter ID#')
extra = ['Modeled Lean', 'Primary Lean Index (-1 D to +1 R)', 'Primary Ballots Taken',
         'Modeled R Probability', 'Model Basis', 'Town/Ward 2024 Gov R Share',
         'Ballot Status', 'County', 'House District', 'Floterial', 'Senate District']

o = xlsxwriter.Workbook(out)
sh = o.add_worksheet(ws.title[:31])
bold = o.add_format({'bold': True}); pct = o.add_format({'num_format': '0.0%'})
sh.write_row(0, 0, header + extra, bold)
n = 0; missing = 0
for r in rows:
    if not any(x is not None for x in r): continue
    n += 1
    r = ['' if x is None else (x.strftime('%m/%d/%Y') if hasattr(x, 'strftime') else x) for x in r]
    sh.write_row(n, 0, r)
    vid = str(r[idcol]).strip().zfill(9) if str(r[idcol]).strip().isdigit() else str(r[idcol]).strip()
    v = S.get(vid) or S.get(str(r[idcol]).strip())
    if not v:
        missing += 1; continue
    p, basis, ts = model(v)
    e = enr.get(v['voter_id']) or {}
    status = 'Returned' if v['returned'] else ('Mailed, not back' if v['mailed'] else 'Requested, not mailed')
    c = len(header)
    sh.write(n, c, v['bucket'])
    if has(v): sh.write_number(n, c + 1, round(float(v['idx']), 3))
    sh.write_number(n, c + 2, int(v['nhist'] or 0))
    sh.write_number(n, c + 3, round(p, 3), pct)
    sh.write(n, c + 4, basis)
    sh.write_number(n, c + 5, round(ts, 3), pct)
    sh.write(n, c + 6, status)
    sh.write(n, c + 7, (e.get('county') or '').title())
    sh.write(n, c + 8, e.get('house') or '')
    sh.write(n, c + 9, e.get('flot') or '')
    sh.write(n, c + 10, e.get('senate') or '')
sh.freeze_panes(1, 0); sh.autofilter(0, 0, n, len(header) + len(extra) - 1)
o.close()
print(f"rows {n:,}, unmatched {missing}, shift {shift:+.3f}")
