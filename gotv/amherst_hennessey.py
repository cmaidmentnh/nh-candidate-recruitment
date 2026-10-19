"""Runs ON the secondary (voter file). Amherst door list for Jason Hennessey (HI34), 19 Oct 2026:
hard Republicans (registered R, or undeclared with 2+ Republican primary ballots and no Democratic
ones, 2016 on, 2026 primary included) who voted in 2 or 3 of the last 4 generals and skipped 2022,
minus any household with a strong Democrat, minus returned absentees. Also writes the full active
Amherst file with every flag so he can filter it himself.
python3 amherst_hennessey.py DIR   (DIR holds absentee_returned_ids.txt)"""
import csv, subprocess, sys, io, collections
D = sys.argv[1]
voted = {l.strip() for l in open(f"{D}/absentee_returned_ids.txt") if l.strip()}
sql = r"""
copy (
with h as (
  select id_voter,
    bool_or(election_date='2024-11-05' and election_name ilike '%GENERAL%') g24,
    bool_or(election_date='2022-11-08' and election_name ilike '%GENERAL%') g22,
    bool_or(election_date='2020-11-03' and election_name ilike '%GENERAL%') g20,
    bool_or(election_date='2018-11-06' and election_name ilike '%GENERAL%') g18,
    bool_or(election_date='2026-09-08' and election_name ilike '%STATE PRIMARY%') p26,
    max(case when election_date='2026-09-08' and election_name ilike '%STATE PRIMARY%' then upper(cd_part_voted) end) p26_party,
    count(*) filter (where election_name ilike '%PRIMARY%' and upper(cd_part_voted) like 'REP%') rp,
    count(*) filter (where election_name ilike '%PRIMARY%' and upper(cd_part_voted) like 'DEM%') dp
  from voterhistory where election_date>='2016-01-01' group by id_voter)
select s.id_voter, s.nm_first, s.nm_last, s.nm_suff, s.cd_party, s.house_base, s.house_flot,
  s.ad_num, s.ad_num_suffix_a, s.ad_str1, s.ad_unit, s.ad_city, s.ad_zip5,
  coalesce(p.primary_phone, p.mobile, p.landline) phone, p.mobile,
  coalesce(h.g24,false) g24, coalesce(h.g22,false) g22, coalesce(h.g20,false) g20, coalesce(h.g18,false) g18,
  coalesce(h.p26,false) p26, h.p26_party, coalesce(h.rp,0) rp, coalesce(h.dp,0) dp
from statewidechecklist s left join h using (id_voter)
left join voter_phones p using (id_voter)
where s.active and upper(s.ad_city)='AMHERST'
) to stdout with csv header"""
out = subprocess.run(["sudo", "-u", "postgres", "psql", "-X", "-d", "election_data", "-c", sql],
                     capture_output=True, text=True, check=True).stdout
rows = list(csv.DictReader(io.StringIO(out)))
T = lambda x: x == 't'
for r in rows:
    rp, dp = int(r['rp']), int(r['dp'])
    r['hardR'] = r['cd_party'] == 'REP' or (r['cd_party'] == 'UND' and rp >= 2 and dp == 0)
    r['strongD'] = r['cd_party'] == 'DEM' or (r['cd_party'] == 'UND' and dp >= 2 and rp == 0)
    r['gens'] = sum(T(r[k]) for k in ('g24', 'g22', 'g20', 'g18'))
    r['hh'] = ((r['ad_num'] or '') + (r['ad_num_suffix_a'] or ''), (r['ad_str1'] or '').upper(), (r['ad_unit'] or '').upper())
    r['absentee'] = r['id_voter'] in voted
hh_dem = {r['hh'] for r in rows if r['strongD']}
for r in rows: r['demhh'] = r['hh'] in hh_dem

def num(r):
    try: return int(''.join(c for c in (r['ad_num'] or '') if c.isdigit()) or 0)
    except: return 0
def addr(r):
    return " ".join(x for x in [(r['ad_num'] or '') + (r['ad_num_suffix_a'] or ''), r['ad_str1'] or '',
                                ("Unit " + r['ad_unit']) if r['ad_unit'] else ''] if x).strip().title()
yn = lambda b: "Y" if b else "-"
party = lambda r: {"REP": "Republican", "DEM": "Democrat"}.get(r['cd_party'], "Undeclared")
p26 = lambda r: ({"REP": "R", "DEM": "D"}.get((r['p26_party'] or '')[:3], "Y")) if T(r['p26']) else "-"
HEAD = ["First", "Last", "Party", "Street Address", "Town", "ZIP", "Phone", "Mobile",
        "Generals voted (of 4)", "Voted 2024", "Voted 2022", "Voted 2020", "Voted 2018", "Voted 2026 primary",
        "R primary ballots", "D primary ballots"]
def base(r):
    return [(r['nm_first'] or '').title(), ((r['nm_last'] or '') + (' ' + r['nm_suff'] if r['nm_suff'] else '')).title(),
            party(r), addr(r), (r['ad_city'] or '').title(), r['ad_zip5'], r['phone'] or '', r['mobile'] or '',
            r['gens'], yn(T(r['g24'])), yn(T(r['g22'])), yn(T(r['g20'])), yn(T(r['g18'])), p26(r), r['rp'], r['dp']]

tgt = [r for r in rows if r['hardR'] and r['gens'] in (2, 3) and not T(r['g22']) and not r['demhh'] and not r['absentee']]
tgt.sort(key=lambda r: (-r['gens'], not T(r['p26']), (r['ad_str1'] or ''), num(r)))
with open(f"{D}/amherst_targets.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(HEAD + ["Voter ID"])
    for r in tgt: w.writerow(base(r) + [r['id_voter']])
rows.sort(key=lambda r: ((r['ad_str1'] or ''), num(r), r['nm_last'] or ''))
with open(f"{D}/amherst_all.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(HEAD + ["Hard R", "Strong D", "Strong D in household", "Absentee returned", "House district", "Voter ID"])
    for r in rows: w.writerow(base(r) + [yn(r['hardR']), yn(r['strongD']), yn(r['demhh']), yn(r['absentee']),
                                         r['house_base'], r['id_voter']])
c = collections.Counter
hardR_skip22_23 = [r for r in rows if r['hardR'] and r['gens'] in (2, 3) and not T(r['g22'])]
print("amherst active", len(rows), "hardR", sum(r['hardR'] for r in rows), "strongD", sum(r['strongD'] for r in rows))
print("hardR 2-3/4 skipped22", len(hardR_skip22_23), "minus demhh", sum(1 for r in hardR_skip22_23 if r['demhh']),
      "minus absentee", sum(1 for r in hardR_skip22_23 if r['absentee'] and not r['demhh']))
print("targets", len(tgt), "by gens", c(r['gens'] for r in tgt), "voted p26", sum(T(r['p26']) for r in tgt),
      "with phone", sum(1 for r in tgt if r['phone']), "districts", c(r['house_base'] for r in tgt))
