#!/usr/bin/env python3
"""
Check specific districts to understand the discrepancy
Compare historical winner data with our projections
"""

import pandas as pd
import json

# Load current district structure
with open('current_district_structure.json', 'r') as f:
    current_districts = json.load(f)

# Load 2022 results to see current performance
df_2022 = pd.read_csv('2022_nh_all_results_comprehensive.csv')

# Load historical winners
winners_2016 = json.load(open('nh_election_data/2016_winners.json'))
winners_2018 = json.load(open('nh_election_data/2018_winners.json'))
winners_2020 = json.load(open('nh_election_data/2020_winners.json'))

# Check some specific examples
print("CHECKING SPECIFIC DISTRICTS")
print("="*80)

# Let's look at districts that should be strongly R
strong_r_districts = ['Belknap-1', 'Carroll-5', 'Rockingham-34', 'Rockingham-35']

for dist_key in strong_r_districts:
    if dist_key not in current_districts:
        continue
        
    county, dist_num = dist_key.split('-')
    print(f"\n{dist_key} - Towns: {', '.join(current_districts[dist_key])}")
    
    # Check 2022 results
    dist_2022 = df_2022[(df_2022['county'] == county) & (df_2022['district'] == int(dist_num))]
    if not dist_2022.empty:
        r_votes_2022 = dist_2022[dist_2022['party'] == 'R']['votes'].sum()
        d_votes_2022 = dist_2022[dist_2022['party'] == 'D']['votes'].sum()
        total_2022 = r_votes_2022 + d_votes_2022
        if total_2022 > 0:
            r_share_2022 = r_votes_2022 / total_2022
            print(f"  2022: R {r_votes_2022:,} ({r_share_2022:.1%}) vs D {d_votes_2022:,}")
    
    # Check our predictions for historical years
    for year in [2016, 2018, 2020]:
        pred_df = pd.read_csv(f'{year}_redistricting_corrected.csv')
        pred = pred_df[pred_df['district'] == dist_key]
        if not pred.empty:
            r_seats = pred['r_seats'].iloc[0]
            d_seats = pred['d_seats'].iloc[0]
            r_share = pred['r_share'].iloc[0]
            print(f"  {year}: Predicted {r_seats}R-{d_seats}D (R share: {r_share:.1%})")

# Now let's check what the problem might be
print("\n\nCHECKING VOTE CALCULATION METHOD")
print("="*80)

# Pick a specific town and year to trace through
test_town = 'Bedford'
test_year = 2016

print(f"\nTracing {test_town} in {test_year}:")

# Load raw data
df_2016 = pd.read_csv('nh_election_data/2016_parsed_results.csv')
bedford_2016 = df_2016[df_2016['town'] == test_town]

print(f"\nRaw data for {test_town}:")
print(bedford_2016[['district', 'candidate', 'party', 'votes']])

# Sum by party
r_total = bedford_2016[bedford_2016['party'] == 'R']['votes'].sum()
d_total = bedford_2016[bedford_2016['party'] == 'D']['votes'].sum()
r_count = len(bedford_2016[bedford_2016['party'] == 'R'])
d_count = len(bedford_2016[bedford_2016['party'] == 'D'])

print(f"\nTotals: R votes: {r_total:,} ({r_count} candidates), D votes: {d_total:,} ({d_count} candidates)")
print(f"Average per candidate: R: {r_total/r_count:,.0f}, D: {d_total/d_count:,.0f}")

# What district was Bedford in historically?
historical_district = bedford_2016['district'].iloc[0] if not bedford_2016.empty else 'Unknown'
print(f"\nHistorical district: {historical_district}")

# Who won in that historical district?
if historical_district in winners_2016:
    hist_winners = winners_2016[historical_district]['winners']
    print(f"Historical winners in {historical_district}:")
    for w in hist_winners:
        print(f"  {w.get('candidate', 'Unknown')} ({w['party']})")

# What current district is Bedford in?
current_dist = None
for dist_key, towns in current_districts.items():
    if test_town in towns:
        current_dist = dist_key
        break

if current_dist:
    print(f"\nCurrent district: {current_dist}")
    print(f"Other towns in current district: {', '.join([t for t in current_districts[current_dist] if t != test_town])}")

# The key insight: Are we comparing apples to apples?
print("\n\nKEY QUESTIONS:")
print("1. Are historical multi-member districts being compared fairly to current districts?")
print("2. Are we accounting for the fact that voters in multi-member districts vote for multiple candidates?")
print("3. Are towns that were split between districts historically now unified (or vice versa)?")