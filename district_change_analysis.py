#!/usr/bin/env python3
"""
Analyze specific district changes between old and new maps
Focus on where R/D advantages shifted
"""

import pandas as pd
import json
import numpy as np
from collections import defaultdict

print("DISTRICT CHANGE ANALYSIS")
print("="*80)
print("Examining specific changes in district composition\n")

# Load all data
current_districts = json.load(open('current_district_structure.json'))

# Load historical data
hist_2016 = pd.read_csv('nh_election_data/2016_parsed_results.csv')
hist_2018 = pd.read_csv('nh_election_data/2018_parsed_results.csv') 
hist_2020 = pd.read_csv('nh_election_data/2020_parsed_results.csv')

# Load current results
curr_2022 = pd.read_csv('2022_nh_all_results_comprehensive.csv')
curr_2024 = pd.read_csv('2024_nh_all_results_comprehensive.csv')

# Load winners
winners_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
winners_2024 = pd.read_csv('2024_nh_winners_comprehensive.csv')

print("STEP 1: MAP HISTORICAL DISTRICTS TO TOWNS")
print("="*80)

# Build historical district maps
historical_district_maps = {}

for year, df in [(2016, hist_2016), (2018, hist_2018), (2020, hist_2020)]:
    district_map = defaultdict(set)
    
    for _, row in df.iterrows():
        district = row['district']
        town = row['town']
        district_map[district].add(town)
    
    historical_district_maps[year] = dict(district_map)
    print(f"{year}: {len(district_map)} districts")

# Find towns that were split or merged
print("\nAnalyzing town redistricting patterns...")

# For each current district, trace where its towns came from
redistricting_impact = {}

for curr_dist, curr_towns in current_districts.items():
    curr_towns_set = set(curr_towns)
    
    # For each historical year, find which districts these towns were in
    historical_sources = {}
    
    for year in [2016, 2018, 2020]:
        sources = defaultdict(int)
        
        for hist_dist, hist_towns in historical_district_maps[year].items():
            overlap = curr_towns_set & hist_towns
            if overlap:
                sources[hist_dist] = len(overlap)
        
        historical_sources[year] = dict(sources)
    
    redistricting_impact[curr_dist] = {
        'current_towns': list(curr_towns),
        'num_towns': len(curr_towns),
        'historical_sources': historical_sources
    }

# Identify major changes
print("\nIdentifying major redistricting changes...")

major_changes = []

for curr_dist, impact in redistricting_impact.items():
    # Check if this district pulled from multiple historical districts
    for year, sources in impact['historical_sources'].items():
        if len(sources) > 1:
            major_changes.append({
                'current_district': curr_dist,
                'year': year,
                'merged_from': list(sources.keys()),
                'towns_from_each': sources
            })
            break

print(f"Found {len(set(c['current_district'] for c in major_changes))} districts created from mergers")

print("\n" + "="*80)
print("STEP 2: ANALYZE PARTISAN IMPACT OF CHANGES")
print("="*80)

# For key merged/split districts, analyze partisan impact
def get_district_partisan_lean(df, district):
    """Calculate partisan lean of a historical district"""
    dist_data = df[df['district'] == district]
    r_votes = dist_data[dist_data['party'] == 'R']['votes'].sum()
    d_votes = dist_data[dist_data['party'] == 'D']['votes'].sum()
    
    if r_votes + d_votes > 0:
        return r_votes / (r_votes + d_votes)
    return 0.5

# Analyze specific examples
print("\nEXAMPLE DISTRICT CHANGES:")
print("-"*60)

examples_analyzed = 0
for change in major_changes[:10]:  # First 10 examples
    if change['year'] == 2020 and examples_analyzed < 5:
        curr_dist = change['current_district']
        
        print(f"\n{curr_dist}:")
        print(f"  Created from: {', '.join(change['merged_from'])}")
        
        # Get partisan lean of source districts
        source_leans = []
        total_towns = 0
        
        for source_dist in change['merged_from']:
            lean = get_district_partisan_lean(hist_2020, source_dist)
            towns = change['towns_from_each'][source_dist]
            source_leans.append((source_dist, lean, towns))
            total_towns += towns
        
        # Weighted average lean
        weighted_lean = sum(lean * towns for _, lean, towns in source_leans) / total_towns
        
        print(f"  Source districts:")
        for dist, lean, towns in source_leans:
            print(f"    {dist}: {lean:.1%} R ({towns} towns)")
        print(f"  Weighted historical lean: {weighted_lean:.1%} R")
        
        # Get current lean
        county, dist_num = curr_dist.split('-')
        curr_data_2022 = curr_2022[(curr_2022['county'] == county) & (curr_2022['district'] == int(dist_num))]
        r_votes = curr_data_2022[curr_data_2022['party'] == 'R']['votes'].sum()
        d_votes = curr_data_2022[curr_data_2022['party'] == 'D']['votes'].sum()
        
        if r_votes + d_votes > 0:
            current_lean = r_votes / (r_votes + d_votes)
            print(f"  Current lean (2022): {current_lean:.1%} R")
            print(f"  Change: {(current_lean - weighted_lean)*100:+.1f}% for R")
        
        examples_analyzed += 1

print("\n" + "="*80)
print("STEP 3: ANALYZE COMPETITIVE DISTRICT CHANGES")
print("="*80)

# Focus on competitive districts (45-55% range)
competitive_threshold = 0.55

# Find historically competitive districts
hist_competitive = set()

for year, df in [(2016, hist_2016), (2018, hist_2018), (2020, hist_2020)]:
    for district in df['district'].unique():
        lean = get_district_partisan_lean(df, district)
        if abs(lean - 0.5) < (competitive_threshold - 0.5):
            hist_competitive.add(district)

print(f"Found {len(hist_competitive)} historically competitive districts")

# Find currently competitive districts
curr_competitive = set()

for dist_key in current_districts.keys():
    county, dist_num = dist_key.split('-')
    
    # Get 2022 lean
    dist_data = curr_2022[(curr_2022['county'] == county) & (curr_2022['district'] == int(dist_num))]
    r_votes = dist_data[dist_data['party'] == 'R']['votes'].sum()
    d_votes = dist_data[dist_data['party'] == 'D']['votes'].sum()
    
    if r_votes + d_votes > 0:
        lean = r_votes / (r_votes + d_votes)
        if abs(lean - 0.5) < (competitive_threshold - 0.5):
            curr_competitive.add(dist_key)

print(f"Found {len(curr_competitive)} currently competitive districts")

print("\n" + "="*80)
print("STEP 4: EFFICIENCY GAP ANALYSIS")
print("="*80)

# Calculate efficiency gap for old vs new maps
def calculate_efficiency_gap(year_data, winners_data):
    """Calculate wasted votes and efficiency gap"""
    
    # For each district, calculate wasted votes
    r_wasted_total = 0
    d_wasted_total = 0
    
    for district in year_data['district'].unique():
        dist_data = year_data[year_data['district'] == district]
        
        r_votes = dist_data[dist_data['party'] == 'R']['votes'].sum()
        d_votes = dist_data[dist_data['party'] == 'D']['votes'].sum()
        total_votes = r_votes + d_votes
        
        if total_votes > 0:
            # Determine winner (simplified - assumes party with more votes wins all seats)
            if r_votes > d_votes:
                # R wins - D votes all wasted, R votes over 50% wasted
                d_wasted = d_votes
                r_wasted = r_votes - (total_votes / 2 + 1)
            else:
                # D wins - R votes all wasted, D votes over 50% wasted
                r_wasted = r_votes
                d_wasted = d_votes - (total_votes / 2 + 1)
            
            r_wasted_total += max(0, r_wasted)
            d_wasted_total += max(0, d_wasted)
    
    total_votes = r_wasted_total + d_wasted_total
    if total_votes > 0:
        efficiency_gap = (d_wasted_total - r_wasted_total) / total_votes
    else:
        efficiency_gap = 0
    
    return efficiency_gap

# Calculate for historical years
print("\nEfficiency Gap Analysis:")
print("(Positive = D disadvantage, Negative = R disadvantage)")

for year, df in [(2016, hist_2016), (2018, hist_2018), (2020, hist_2020)]:
    gap = calculate_efficiency_gap(df, None)
    print(f"  {year} historical districts: {gap:+.3f}")

# Calculate for current districts using 2022 data
gap_2022 = calculate_efficiency_gap(curr_2022, None)
print(f"  2022 current districts: {gap_2022:+.3f}")

print("\n" + "="*80)
print("STEP 5: FINAL ASSESSMENT")
print("="*80)

# Count specific types of changes
packing_changes = 0  # Safe districts made safer
cracking_changes = 0  # Competitive districts made safe
efficiency_changes = 0  # Districts redrawn for efficiency

# This would require detailed town-by-town analysis
# For now, use aggregate statistics

print("\nSUMMARY OF REDISTRICTING IMPACT:")
print("-"*60)

# Based on all analyses
print("\n1. District Competitiveness:")
print(f"   - Historical competitive districts: ~{len(hist_competitive)}")
print(f"   - Current competitive districts: {len(curr_competitive)}")

print("\n2. Major District Changes:")
print(f"   - Districts created from mergers: {len(set(c['current_district'] for c in major_changes))}")
print(f"   - Affected approximately {len(major_changes)*2} historical districts")

print("\n3. Efficiency Gap:")
print(f"   - Historical average: {np.mean([calculate_efficiency_gap(df, None) for _, df in [(2016, hist_2016), (2018, hist_2018), (2020, hist_2020)]]):.3f}")
print(f"   - Current (2022): {gap_2022:.3f}")

# Final conclusion based on evidence
print("\n" + "="*80)
print("CONCLUSION")
print("="*80)

# Determine the likely impact
competitive_change = len(curr_competitive) - len(hist_competitive)
efficiency_change = gap_2022 - np.mean([calculate_efficiency_gap(df, None) for _, df in [(2016, hist_2016), (2018, hist_2018), (2020, hist_2020)]])

print("\nBased on district-level analysis:")

if competitive_change < 0:
    print(f"- Fewer competitive districts ({competitive_change})")
else:
    print(f"- More competitive districts (+{competitive_change})")

if efficiency_change < 0:
    print(f"- Efficiency gap moved in R's favor ({efficiency_change:.3f})")
else:
    print(f"- Efficiency gap moved in D's favor (+{efficiency_change:.3f})")

# The most direct test: actual outcomes
print("\n" + "="*80)
print("ACTUAL OUTCOME TEST")
print("="*80)

# Compare actual R performance in similar environments
# 2016 was R+6.5%, 2020 was R+3.2%, 2022 was R+0.4%

# Interpolate expected 2022 result based on 2016 and 2020
env_2016 = 0.565  # 56.5% R
env_2020 = 0.532  # 53.2% R
env_2022 = 0.504  # 50.4% R

# Linear interpolation
expected_2022_r_pct = env_2020 + (env_2022 - env_2020) * (env_2020 - env_2016) / (env_2020 - env_2016)
expected_2022_r_seats = int(expected_2022_r_pct * 400)

actual_2022_r_seats = 201

print(f"\nExpected R seats in 2022 based on historical trend: ~{expected_2022_r_seats}")
print(f"Actual R seats in 2022: {actual_2022_r_seats}")
print(f"Difference: {actual_2022_r_seats - expected_2022_r_seats:+d}")

if actual_2022_r_seats > expected_2022_r_seats:
    print(f"\nRepublicans performed {actual_2022_r_seats - expected_2022_r_seats} seats BETTER than expected")
    print("This suggests the new districts may favor Republicans")
else:
    print(f"\nRepublicans performed {expected_2022_r_seats - actual_2022_r_seats} seats WORSE than expected")
    print("This suggests the new districts may not favor Republicans as much as historical districts")