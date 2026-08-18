#!/usr/bin/env python3
"""Weekly absentee-ballot pipeline: from the SOS spreadsheet to per-district
Republican-ballot files and a candidate send plan.

Usage:
  python3 run_pipeline.py --xlsx "~/Downloads/STATE PRIMARY ABSENTEE BALLOT AS OF 08-13-2026.xlsx" --asof 2026-08-13

Produces runs/<asof>/ with every intermediate, absentee_by_district/ files,
and send_plan.json. Then send with:
  python3 send_absentee_blast.py --run runs/<asof> --asof "13 August"            # dry run
  python3 send_absentee_blast.py --run runs/<asof> --asof "13 August" --send     # live

The mechanics replicate the 2026-08-06 send (recovered from that session's
transcript): flatten the SOS file, enrich from the voter DB on the secondary
droplet, assign House districts from town/ward via nh-election-results,
attach CRM emails and phones, score partisan lean from state-primary history,
cut per-district CSVs of Republican-ballot requesters, and plan one email per
R House candidate with their district's file attached.
"""
import argparse, collections, csv, datetime, json, os, shutil, sqlite3, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ELECTIONS_DB = os.path.expanduser("~/nh-election-results/nh_elections.db")
SSH_KEY = os.path.expanduser("~/ubuntu-key")
PRIMARY = "root@138.197.20.97"
SECONDARY = "root@138.197.36.143"


def sh(cmd, **kw):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"FAILED: {cmd[:120]}\n{r.stderr[-2000:]}")
    return r.stdout


_NOISE = ('deprecat', 'warn', 'authlib', 'storage', 'compatible', 'not deployed')


def _ssh_python(ssh_args, remote_cmd, script):
    # The script goes over stdin — no shell-quoting of Python source.
    r = subprocess.run(['ssh', '-i', SSH_KEY] + ssh_args + [remote_cmd],
                       input=script, capture_output=True, text=True)
    out = '\n'.join(l for l in (r.stdout + r.stderr).splitlines()
                    if l.strip() and not any(n in l.lower() for n in _NOISE))
    if r.returncode != 0:
        sys.exit(f"FAILED remote python ({remote_cmd}):\n{out[-2000:]}")
    return out


def ssh_secondary(script):
    return _ssh_python(['-J', PRIMARY, SECONDARY],
                       'cd /opt/nh-voter-api && python3 -', script)


def ssh_primary_recruitment(script):
    return _ssh_python([PRIMARY],
                       'cd /opt/nh-candidate-recruitment && venv/bin/python -', script)


def scp(src, dst):
    sh(f"scp -q -i {SSH_KEY} -o ProxyJump={PRIMARY} {src} {dst}")


def flatten(xlsx, rundir):
    import openpyxl
    ws = openpyxl.load_workbook(xlsx, read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(h) if h else '' for h in rows[0]]
    d = [dict(zip(hdr, r)) for r in rows[1:] if any(r)]

    def s(v):
        return '' if v is None else str(v).strip()

    def dt(v):
        if isinstance(v, datetime.datetime):
            return v.strftime('%Y-%m-%d')
        if not v:
            return ''
        try:
            return datetime.datetime.strptime(str(v), '%m/%d/%Y').strftime('%Y-%m-%d')
        except Exception:
            return str(v)

    out = []
    for x in d:
        out.append(dict(
            voter_id=s(x['Voter ID#']), town=s(x['City/Town']), ward=s(x['Ward/District']),
            last=s(x['Last Name']), first=s(x['First Name']), mid=s(x['Middle Name']),
            suf=s(x['Suffix']),
            addr=' '.join(f for f in [s(x['Street Number']), s(x['Address Suffix']),
                                      s(x['Street Name']), s(x['Apartment/Unit Number'])] if f),
            reg=s(x['Party Registered']), choice=s(x['Party Choice']),
            requested=dt(x['Date Requested']), mailed=dt(x['Date Mailed']),
            returned=dt(x['Date Envelope Returned'])))
    with open(f'{rundir}/absentee_flat.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)
    print(f"flattened {len(out):,} rows "
          f"(returned {sum(1 for x in out if x['returned'])}, "
          f"mailed-not-back {sum(1 for x in out if x['mailed'] and not x['returned'])}, "
          f"not-mailed {sum(1 for x in out if not x['mailed'])})")


def pull_history(rundir):
    scp(f'{rundir}/absentee_flat.csv', f'{SECONDARY}:/tmp/absentee_flat.csv')
    print(ssh_secondary("""import os, csv, psycopg2, json, collections
from dotenv import load_dotenv
load_dotenv('/opt/nh-voter-api/.env')
c=psycopg2.connect(host='localhost',database='election_data',user='voter_api_readonly',
  password=os.environ.get('VOTER_DB_PASSWORD','voter_readonly_2026'))
cu=c.cursor()
ids=[r['voter_id'] for r in csv.DictReader(open('/tmp/absentee_flat.csv'))]
cu.execute('''SELECT id_voter, EXTRACT(YEAR FROM election_date)::int,
   UPPER(LEFT(cd_part_voted,3)) FROM voterhistory
  WHERE id_voter = ANY(%s) AND election_name ILIKE '%%STATE PRIMARY%%'
    AND UPPER(LEFT(cd_part_voted,3)) IN ('REP','DEM')
  ORDER BY id_voter, election_date''',(ids,))
h=collections.defaultdict(list)
for v,y,p in cu.fetchall(): h[v].append([y, 'R' if p=='REP' else 'D'])
print('voters with state-primary history:', len(h), 'of', len(ids))
json.dump(h, open('/tmp/absentee_hist.json','w'))
c.close()"""))
    scp(f'{SECONDARY}:/tmp/absentee_hist.json', rundir)


def pull_enrichment(rundir):
    print(ssh_secondary("""import os, csv, psycopg2, json
from dotenv import load_dotenv
load_dotenv('/opt/nh-voter-api/.env')
c=psycopg2.connect(host='localhost',database='election_data',user='voter_api_readonly',
  password=os.environ.get('VOTER_DB_PASSWORD','voter_readonly_2026'))
cu=c.cursor()
ids=[r['voter_id'] for r in csv.DictReader(open('/tmp/absentee_flat.csv'))]
cu.execute('''SELECT s.id_voter, s.house_district, s.floterial_district, s.senate_district,
   s.ad_city, s.ward, s.county, p.mobile, p.landline, p.primary_phone
 FROM statewidechecklist s LEFT JOIN voter_phones p ON p.id_voter = s.id_voter
 WHERE s.id_voter = ANY(%s)''',(ids,))
out={}
for r in cu.fetchall():
    out[r[0]]=dict(house=r[1], flot=r[2], senate=r[3], city=r[4], ward=r[5], county=r[6],
                   mobile=r[7], landline=r[8], phone=r[9])
print('matched to checklist:', len(out), 'of', len(ids))
json.dump(out, open('/tmp/absentee_enrich.json','w'))
c.close()"""))
    scp(f'{SECONDARY}:/tmp/absentee_enrich.json', rundir)


def score_lean(rundir):
    A = list(csv.DictReader(open(f'{rundir}/absentee_flat.csv')))
    H = json.load(open(f'{rundir}/absentee_hist.json'))
    W = {2024: 1.0, 2022: 0.7, 2020: 0.5, 2018: 0.35}   # recent primaries count for more

    def lean(v):
        hist = H.get(v['voter_id'], [])
        r = sum(W.get(y, 0.3) for y, p in hist if p == 'R')
        d = sum(W.get(y, 0.3) for y, p in hist if p == 'D')
        if r + d == 0:
            return None, 0
        return (r - d) / (r + d), len(hist)

    def bucket(v):
        reg = v['reg']; idx, n = lean(v)
        if reg == 'REP':
            return 'Registered Republican'
        if reg == 'DEM':
            return 'Registered Democrat'
        if idx is None:
            return 'Undeclared, no primary history'
        if idx >= 0.6:
            return 'Undeclared, leans Republican'
        if idx <= -0.6:
            return 'Undeclared, leans Democrat'
        return 'Undeclared, genuinely swing'

    for v in A:
        v['bucket'] = bucket(v)
        v['idx'], v['nhist'] = lean(v)
    with open(f'{rundir}/absentee_scored.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(A[0].keys()))
        w.writeheader(); w.writerows(A)
    c = collections.Counter(v['bucket'] for v in A)
    print("lean:", dict(c))


def assign_districts(rundir):
    c = sqlite3.connect(ELECTIONS_DB)
    m = collections.defaultdict(list)
    for cty, dist, muni in c.execute(
            """SELECT county,district,municipality FROM district_compositions
               WHERE redistricting_cycle='2022-2030' AND office='State Representative'"""):
        m[muni.upper()].append(f"{cty} {dist}")
    A = list(csv.DictReader(open(f'{rundir}/absentee_flat.csv')))

    def districts(a):
        t = a['town'].upper().strip()
        w = str(a['ward'] or '').strip().lstrip('0')
        keys = []
        if w and f"{t} WARD {w}" in m:
            keys.append(f"{t} WARD {w}")
        if t in m:
            keys.append(t)
        if not keys:            # a city whose wards are listed but ward missing/00
            keys = [k for k in m if k.startswith(t + ' WARD')]
        out = []
        for k in keys:
            for d in m[k]:
                if d not in out:
                    out.append(d)
        return out

    un = 0
    for a in A:
        a['districts'] = districts(a)
        if not a['districts']:
            un += 1
    json.dump({a['voter_id']: a['districts'] for a in A},
              open(f'{rundir}/absentee_districts.json', 'w'))
    per = collections.Counter(d for a in A for d in a['districts'])
    print(f"district assignment: {len(A):,} voters, {un} unassigned, {len(per)} districts touched")
    if un:
        missing = sorted({a['town'] for a in A if not a['districts']})
        print("  UNASSIGNED TOWNS:", missing[:15])


def pull_crm_emails(rundir):
    ids = [r['voter_id'] for r in csv.DictReader(open(f'{rundir}/absentee_flat.csv'))]
    json.dump(ids, open(f'{rundir}/ab_ids.json', 'w'))
    sh(f"scp -q -i {SSH_KEY} {rundir}/ab_ids.json {PRIMARY}:/tmp/")
    print(ssh_primary_recruitment("""import os, json, psycopg2
from dotenv import load_dotenv
load_dotenv('/opt/nh-candidate-recruitment/.env')
ids=json.load(open('/tmp/ab_ids.json'))
c=psycopg2.connect(os.environ['CRM_DATABASE_URL']); cu=c.cursor()
cu.execute('''SELECT voter_id, email, email2, email_status FROM contacts
  WHERE voter_id = ANY(%s) AND email IS NOT NULL AND email <> %s''',(ids,''))
out={}
for v,e,e2,st in cu.fetchall():
    if v and v not in out: out[v]={'email':e,'email2':e2,'status':st}
print('absentee voters with a CRM email:', len(out), 'of', len(ids))
json.dump(out, open('/tmp/ab_emails.json','w'))
c.close()"""))
    sh(f"scp -q -i {SSH_KEY} {PRIMARY}:/tmp/ab_emails.json {rundir}/")


def build_district_files(rundir):
    A = list(csv.DictReader(open(f'{rundir}/absentee_scored.csv')))
    nraw = len(A)
    best = {}

    def rank(a):
        return (bool(a['returned']), bool(a['mailed']), a['requested'] or '')

    for a in A:
        k = a['voter_id']
        if k not in best or rank(a) > rank(best[k]):
            best[k] = a
    A = list(best.values())
    print(f"deduped to {len(A):,} distinct voters (was {nraw:,} rows)")
    DS = json.load(open(f'{rundir}/absentee_districts.json'))
    EN = json.load(open(f'{rundir}/absentee_enrich.json'))
    EM = json.load(open(f'{rundir}/ab_emails.json'))

    def phone(v):
        e = EN.get(v, {})
        return e.get('phone') or e.get('mobile') or e.get('landline') or ''

    def bkt(a):
        if a['returned']:
            return 'ALREADY VOTED'
        if a['mailed']:
            return 'BALLOT IN HAND'
        return 'REQUESTED, NOT YET MAILED'

    per = collections.defaultdict(list)
    for a in A:
        a['bkt'] = bkt(a)
        for d in DS.get(a['voter_id'], []):
            per[d].append(a)
    c = sqlite3.connect(ELECTIONS_DB)
    cands = collections.defaultdict(list)
    for cty, dist, nm in c.execute(
            """SELECT ra.county,ra.district,cd.name FROM race_candidates rc
               JOIN races ra ON ra.id=rc.race_id JOIN elections e ON e.id=ra.election_id
               JOIN candidates cd ON cd.id=rc.candidate_id
               WHERE e.year=2026 AND ra.office_id=7 AND rc.party='Republican'"""):
        cands[f"{cty} {dist}"].append(nm)
    outdir = f'{rundir}/absentee_by_district'
    shutil.rmtree(outdir, ignore_errors=True)
    os.makedirs(outdir)
    F = ['Bucket', 'Last Name', 'First Name', 'Address', 'Town', 'Ward', 'Registered',
         'Ballot Requested', 'Lean', 'Date Requested', 'Date Mailed', 'Date Returned',
         'Phone', 'Email', 'Voter ID']
    summary = []
    for d, rows in sorted(per.items()):
        rep = [a for a in rows if a['choice'] == 'REP']
        if not rep:
            continue
        rep.sort(key=lambda a: ({'BALLOT IN HAND': 0, 'REQUESTED, NOT YET MAILED': 1,
                                 'ALREADY VOTED': 2}[a['bkt']], a['last'], a['first']))
        with open(f"{outdir}/{d.replace(' ', '_')}_REP_absentee.csv", 'w', newline='') as f:
            w = csv.writer(f); w.writerow(F)
            for a in rep:
                w.writerow([a['bkt'], a['last'], a['first'], a['addr'], a['town'], a['ward'],
                            a['reg'], a['choice'], a['bucket'], a['requested'], a['mailed'],
                            a['returned'], phone(a['voter_id']),
                            (EM.get(a['voter_id']) or {}).get('email', ''), a['voter_id']])
        b = collections.Counter(a['bkt'] for a in rep)
        summary.append(dict(
            district=d, candidates='; '.join(cands.get(d, [])) or 'NO R FILED',
            n_R_candidates=len(cands.get(d, [])), voters=len(rep),
            ballot_in_hand=b['BALLOT IN HAND'], not_yet_mailed=b['REQUESTED, NOT YET MAILED'],
            already_voted=b['ALREADY VOTED'],
            with_phone=sum(1 for a in rep if phone(a['voter_id']))))
    summary.sort(key=lambda r: -r['ballot_in_hand'])
    with open(f'{outdir}/_SUMMARY.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader(); w.writerows(summary)
    print(f"{len(summary)} district files; REP-ballot voters statewide: "
          f"{sum(1 for a in A if a['choice'] == 'REP')}; "
          f"in hand {sum(r['ballot_in_hand'] for r in summary)}, "
          f"not mailed {sum(r['not_yet_mailed'] for r in summary)}, "
          f"voted {sum(r['already_voted'] for r in summary)} (incl. floterial duplication)")


def pull_candidates(rundir):
    print(ssh_primary_recruitment("""import app as A, json
conn=A.get_db_connection(); cur=conn.cursor()
cur.execute('''SELECT f.district_code, c.candidate_id, f.first_name, f.last_name,
   c.email, c.email1, c.email2, c.dead_email, c.unsubscribed_email
 FROM filings f JOIN candidates c ON c.candidate_id=f.candidate_id
 WHERE f.election_year=2026 AND f.party='R' AND f.office='State Representative'
 ORDER BY f.district_code''')
rows=[dict(zip(['district','cid','first','last','email','email1','email2','dead','unsub'],r)) for r in cur.fetchall()]
def pick(r):
    for e in (r['email'], r['email1'], r['email2']):
        if e and e.strip() and 'gc.nh.gov' not in e.lower(): return e.strip()
    for e in (r['email'], r['email1'], r['email2']):
        if e and e.strip(): return e.strip()
    return None
for r in rows: r['to']=pick(r)
print('R State Rep filings:', len(rows),
      ' usable email:', sum(1 for r in rows if r['to'] and not r['dead'] and not r['unsub']))
json.dump(rows, open('/tmp/r_house_candidates.json','w'), default=str)
cur.close(); A.release_db_connection(conn)"""))
    sh(f"scp -q -i {SSH_KEY} {PRIMARY}:/tmp/r_house_candidates.json {rundir}/")


def build_plan(rundir):
    C = json.load(open(f'{rundir}/r_house_candidates.json'))
    outdir = f'{rundir}/absentee_by_district'
    have = {f[:-len('_REP_absentee.csv')].replace('_', ' ')
            for f in os.listdir(outdir) if f.endswith('_REP_absentee.csv')}
    plan = []
    skip = collections.Counter()
    for c in C:
        if c['district'] not in have:
            skip['district has no Republican-ballot absentees'] += 1; continue
        if not c['to']:
            skip['no email address on file'] += 1; continue
        if c['dead']:
            skip['email flagged dead (already bounced)'] += 1; continue
        plan.append(c)
    json.dump(plan, open(f'{rundir}/send_plan.json', 'w'))
    tot = sum(len(list(csv.DictReader(open(
        f"{outdir}/{c['district'].replace(' ', '_')}_REP_absentee.csv")))) for c in plan)
    print(f"SEND PLAN: {len(plan)} candidates across "
          f"{len({c['district'] for c in plan})} districts, {tot:,} voter rows in attachments")
    for k, v in skip.most_common():
        print(f"   skipped, {k}: {v}")
    uns = sum(1 for c in plan if c['unsub'])
    if uns:
        print(f"   ({uns} flagged unsubscribed, included per Chris's standing call from the 8/6 send)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', required=True)
    ap.add_argument('--asof', required=True, help='e.g. 2026-08-13 (names the run dir)')
    a = ap.parse_args()
    rundir = os.path.join(HERE, 'runs', a.asof)
    os.makedirs(rundir, exist_ok=True)
    xlsx = os.path.expanduser(a.xlsx)
    flatten(xlsx, rundir)
    pull_history(rundir)
    pull_enrichment(rundir)
    score_lean(rundir)
    assign_districts(rundir)
    pull_crm_emails(rundir)
    build_district_files(rundir)
    pull_candidates(rundir)
    build_plan(rundir)
    print(f"\nrun dir: {rundir}")


if __name__ == '__main__':
    main()
