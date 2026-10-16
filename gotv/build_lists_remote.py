"""Runs ON the secondary (voter file). Builds one GOTV CSV per House district:
strong Republicans (registered R, or undeclared with 2+ Republican primary ballots and no
Democratic ones) who voted the 2024 general but skipped the 2022 and/or 2018 midterm,
minus anyone whose absentee ballot is already returned.

Voters are placed by checklist town + ward against the official town/ward -> district map
(municipality_districts.psv, from nh_elections.db office 7, floterials included), NOT by the
geocoded house_district/floterial_district columns: those put ~2% of voters in the wrong
district (e.g. Laconia Ward 2 and Meredith voters on the Belknap 5 list, 10/16/26)."""
import csv, os, subprocess, sys, io, collections
D=sys.argv[1]
dists=[l.strip() for l in open(f"{D}/districts.txt") if l.strip()]
voted={l.strip() for l in open(f"{D}/absentee_returned_ids.txt") if l.strip()}
sql=r"""
copy (
with h as (
  select id_voter,
    bool_or(election_date='2024-11-05' and election_name ilike '%GENERAL%') g24,
    bool_or(election_date='2022-11-08' and election_name ilike '%GENERAL%') g22,
    bool_or(election_date='2020-11-03' and election_name ilike '%GENERAL%') g20,
    bool_or(election_date='2018-11-06' and election_name ilike '%GENERAL%') g18,
    count(*) filter (where election_name ilike '%PRIMARY%' and upper(cd_part_voted) like 'REP%') rp,
    count(*) filter (where election_name ilike '%PRIMARY%' and upper(cd_part_voted) like 'DEM%') dp
  from voterhistory where election_date>='2016-01-01' group by id_voter)
select s.id_voter, s.nm_first, s.nm_last, s.nm_suff, s.cd_party, s.house_district, s.floterial_district,
  s.ad_num, s.ad_num_suffix_a, s.ad_str1, s.ad_unit, s.ad_city, s.ad_zip5, s.ward,
  coalesce(p.primary_phone, p.mobile, p.landline) phone, p.mobile,
  h.g24, h.g22, h.g20, h.g18, h.rp, h.dp
from statewidechecklist s join h using (id_voter)
left join voter_phones p using (id_voter)
where s.active and h.g24 and (not h.g22 or not h.g18)
  and (s.cd_party='REP' or (s.cd_party='UND' and h.rp>=2 and h.dp=0))
) to stdout with csv header"""
out=subprocess.run(["sudo","-u","postgres","psql","-X","-d","election_data","-c",sql],capture_output=True,text=True,check=True).stdout
rows=list(csv.DictReader(io.StringIO(out)))
T=lambda x: x=='t'
import re
norm=lambda d: re.sub(r'\s0+(\d)', r' \1', (d or '').strip())
key=lambda town: (town or '').upper().replace("'", "").strip()
dmap=collections.defaultdict(set)
for l in open(f"{D}/municipality_districts.psv"):
    m,county,dn=l.rstrip("\n").split("|")
    mm=re.match(r"(.+?) Ward (\d+)$",m)
    dmap[(key(mm.group(1)),int(mm.group(2))) if mm else (key(m),0)].add(f"{county} {dn}")
by=collections.defaultdict(list); n_voted=0; unmapped=collections.Counter()
for r in rows:
    if r['id_voter'] in voted: n_voted+=1; continue
    w=int(r['ward']) if (r['ward'] or '').strip().isdigit() else 0
    ds=dmap.get((key(r['ad_city']),w)) or dmap.get((key(r['ad_city']),0))   # towns without wards: polling district != ward
    if not ds: unmapped[(r['ad_city'],r['ward'])]+=1; continue
    for d in ds:
        if d in dists: by[d].append(r)
os.makedirs(f"{D}/lists",exist_ok=True)
summ=[]
for d in dists:
    L=by.get(d,[])
    def pri(r):
        if not T(r['g22']) and not T(r['g18']): return (1,"Skipped both midterms")
        if not T(r['g22']): return (2,"Skipped 2022")
        return (3,"Skipped 2018")
    def num(r):
        try: return int(''.join(c for c in (r['ad_num'] or '') if c.isdigit()) or 0)
        except: return 0
    L.sort(key=lambda r:(pri(r)[0], (r['ad_city'] or ''), (r['ad_str1'] or ''), num(r)))
    fn=f"{D}/lists/{d.replace(' ','_')}_GOTV.csv"
    with open(fn,"w",newline="") as f:
        w=csv.writer(f)
        w.writerow(["Priority","First","Last","Party","Street Address","Town","ZIP","Phone","Mobile","Voted 2024","Voted 2022","Voted 2020","Voted 2018","Voter ID"])
        for r in L:
            addr=" ".join(x for x in [(r['ad_num'] or '')+(r['ad_num_suffix_a'] or ''), r['ad_str1'] or '', ("Unit "+r['ad_unit']) if r['ad_unit'] else ''] if x).strip()
            yn=lambda k: "Y" if T(r[k]) else "-"
            w.writerow([pri(r)[1], (r['nm_first'] or '').title(), ((r['nm_last'] or '')+(' '+r['nm_suff'] if r['nm_suff'] else '')).title(),
                        "Republican" if r['cd_party']=='REP' else "Undeclared (votes R)", addr.title(), (r['ad_city'] or '').title(), r['ad_zip5'],
                        r['phone'] or '', r['mobile'] or '', yn('g24'),yn('g22'),yn('g20'),yn('g18'), r['id_voter']])
    c1=sum(1 for r in L if pri(r)[0]==1)
    summ.append((d,len(L),c1,sum(1 for r in L if r['phone'])))
with open(f"{D}/summary.csv","w",newline="") as f:
    w=csv.writer(f); w.writerow(["district","voters","skipped_both","with_phone"]); w.writerows(summ)
print("unmapped",sum(unmapped.values()),unmapped.most_common(10))
print("matched",len(rows),"removed_already_voted",n_voted,"districts",len(summ),"empty",sum(1 for s in summ if s[1]==0))
