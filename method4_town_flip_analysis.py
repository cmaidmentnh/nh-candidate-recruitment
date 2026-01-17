#!/usr/bin/env python3
"""
Method 4: Analyze how many towns flipped between elections
and how they're distributed in current districts
"""

import pandas as pd
import json
from collections import defaultdict

print("METHOD 4: TOWN FLIP ANALYSIS")
print("="*80)

# Load data
current_districts = json.load(open('current_district_structure.json'))

# Track town-level results across years
town_results = {}

for year in [2016, 2018, 2020]:
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    
    for town in df['town'].unique():
        town_data = df[df['town'] == town]
        r_votes = town_data[town_data['party'] == 'R']['votes'].sum()
        d_votes = town_data[town_data['party'] == 'D']['votes'].sum()
        
        if town not in town_results:
            town_results[town] = {}
        
        if r_votes + d_votes > 0:
            town_results[town][year] = {
                'r_votes': r_votes,
                'd_votes': d_votes,
                'winner': 'R' if r_votes > d_votes else 'D',
                'margin': abs(r_votes - d_votes) / (r_votes + d_votes)
            }

# Find towns that flipped
flipped_towns = set()
swing_towns = set()  # Changed winner at least once

for town, years in town_results.items():
    if len(years) >= 2:
        winners = [y['winner'] for y in years.values()]
        if len(set(winners)) > 1:
            swing_towns.add(town)
            
            # Check if flipped between 2016 and 2020
            if 2016 in years and 2020 in years:
                if years[2016]['winner'] != years[2020]['winner']:
                    flipped_towns.add(town)

print(f"Towns that changed party at least once: {len(swing_towns)}")
print(f"Towns that flipped 2016->2020: {len(flipped_towns)}")

# Map swing towns to current districts
print("\n\nSWING TOWNS IN CURRENT DISTRICTS")
print("-"*60)

district_swing_analysis = {}

for dist_key, towns in current_districts.items():
    swing_count = 0
    stable_r = 0
    stable_d = 0
    
    for town in towns:
        # Check variants
        town_data = None
        if town in town_results:
            town_data = town_results[town]
        elif ' Ward ' in town:
            alt = town.replace(' Ward ', ' Wd ')
            if alt in town_results:
                town_data = town_results[alt]
        
        if town_data and len(town_data) >= 2:
            winners = [v['winner'] for v in town_data.values()]
            if len(set(winners)) > 1:
                swing_count += 1
            elif all(w == 'R' for w in winners):
                stable_r += 1
            elif all(w == 'D' for w in winners):
                stable_d += 1
    
    district_swing_analysis[dist_key] = {
        'swing_towns': swing_count,
        'stable_r_towns': stable_r,
        'stable_d_towns': stable_d,
        'total_towns': len(towns)
    }

# Find districts with high swing potential
high_swing_districts = sorted(
    [(k, v['swing_towns']) for k, v in district_swing_analysis.items() if v['swing_towns'] > 0],
    key=lambda x: x[1],
    reverse=True
)

print("\nTop 10 districts with most swing towns:")
for dist, count in high_swing_districts[:10]:
    total = district_swing_analysis[dist]['total_towns']
    print(f"  {dist}: {count} swing towns (out of {total})")

# Analyze concentration of swing towns
print("\n\nSWING TOWN CONCENTRATION")
print("-"*60)

# Are swing towns concentrated in fewer districts or spread out?
districts_with_swing = sum(1 for v in district_swing_analysis.values() if v['swing_towns'] > 0)
total_swing_instances = sum(v['swing_towns'] for v in district_swing_analysis.values())

print(f"Districts containing swing towns: {districts_with_swing} out of {len(current_districts)}")
print(f"Average swing towns per district with any: {total_swing_instances/districts_with_swing:.1f}")

# Compare to stable districts
very_stable_r = sum(1 for v in district_swing_analysis.values() 
                   if v['stable_r_towns'] > v['stable_d_towns'] + v['swing_towns'])
very_stable_d = sum(1 for v in district_swing_analysis.values() 
                   if v['stable_d_towns'] > v['stable_r_towns'] + v['swing_towns'])

print(f"\nVery stable R districts: {very_stable_r}")
print(f"Very stable D districts: {very_stable_d}")
print(f"Mixed/competitive districts: {len(current_districts) - very_stable_r - very_stable_d}")