#!/usr/bin/env python3
"""
Fix vote aggregation to avoid double-counting
Look at actual vote totals by town, not by district assignment
"""

import pandas as pd
import json
import numpy as np
from collections import defaultdict

# Load current district structure
with open('current_district_structure.json', 'r') as f:
    current_districts = json.load(f)

# Load district seats
seats_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
district_seats = {}
for county in seats_2022['county'].unique():
    for district in seats_2022[seats_2022['county'] == county]['district'].unique():
        key = f"{county}-{district}"
        count = len(seats_2022[(seats_2022['county'] == county) & (seats_2022['district'] == district)])
        district_seats[key] = count

print("CORRECTED REDISTRICTING ANALYSIS")
print("="*80)

# For each year, we need to get TOWN-LEVEL vote totals, not district-level
def get_town_vote_totals(year):
    """Get vote totals by town, handling multi-member districts correctly"""
    
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    
    # First, understand the data structure
    print(f"\n{year} data structure:")
    print(f"Total rows: {len(df)}")
    print(f"Unique towns: {df['town'].nunique()}")
    print(f"Unique candidates: {df['candidate'].nunique()}")
    
    # Get vote totals by town and party
    # In multi-member districts, people vote for multiple candidates
    # We need to get the average votes per candidate by party to estimate party strength
    
    town_votes = {}
    
    for town in df['town'].unique():
        town_data = df[df['town'] == town]
        
        # Group by party
        r_data = town_data[town_data['party'] == 'R']
        d_data = town_data[town_data['party'] == 'D']
        other_data = town_data[~town_data['party'].isin(['R', 'D'])]
        
        # For each party, get total votes and number of candidates
        r_votes = r_data['votes'].sum()
        d_votes = d_data['votes'].sum()
        other_votes = other_data['votes'].sum()
        
        r_candidates = len(r_data)
        d_candidates = len(d_data)
        
        # Calculate average votes per candidate (party strength indicator)
        r_avg = r_votes / r_candidates if r_candidates > 0 else 0
        d_avg = d_votes / d_candidates if d_candidates > 0 else 0
        
        town_votes[town] = {
            'r_total': r_votes,
            'd_total': d_votes,
            'other_total': other_votes,
            'r_candidates': r_candidates,
            'd_candidates': d_candidates,
            'r_avg': r_avg,
            'd_avg': d_avg
        }
    
    return town_votes

# Now map to current districts
def analyze_with_town_totals(year, town_votes):
    """Analyze how current districts would perform using town-level data"""
    
    results = []
    
    for district_key, towns in current_districts.items():
        seats = district_seats.get(district_key, 1)
        
        # Aggregate this district's votes from component towns
        district_r_strength = 0  # Sum of R average votes across towns
        district_d_strength = 0  # Sum of D average votes across towns
        towns_found = 0
        
        for town in towns:
            # Try exact match first
            if town in town_votes:
                data = town_votes[town]
                towns_found += 1
            elif ' Ward ' in town:
                # Try alternate format
                alt_town = town.replace(' Ward ', ' Wd ')
                if alt_town in town_votes:
                    data = town_votes[alt_town]
                    towns_found += 1
                else:
                    continue
            else:
                continue
            
            # Add this town's party strength to district total
            district_r_strength += data['r_avg']
            district_d_strength += data['d_avg']
        
        # Determine seat allocation based on relative party strength
        total_strength = district_r_strength + district_d_strength
        
        if total_strength == 0:
            # No data
            r_seats = 0
            d_seats = 0
            unallocated = seats
        else:
            r_share = district_r_strength / total_strength
            
            # Allocate seats
            if seats == 1:
                if r_share > 0.5:
                    r_seats = 1
                    d_seats = 0
                else:
                    r_seats = 0
                    d_seats = 1
                unallocated = 0
            else:
                # Multi-member - winner bonus system
                if r_share > 0.65:
                    r_seats = seats
                    d_seats = 0
                elif r_share > 0.58:
                    r_seats = max(seats - 1, int(seats * 0.75))
                    d_seats = seats - r_seats
                elif r_share > 0.52:
                    r_seats = max(int(seats * 0.6), int(seats * r_share + 0.5))
                    d_seats = seats - r_seats
                elif r_share > 0.48:
                    r_seats = int(seats * r_share + 0.5)
                    d_seats = seats - r_seats
                elif r_share > 0.42:
                    d_seats = max(int(seats * 0.6), int(seats * (1-r_share) + 0.5))
                    r_seats = seats - d_seats
                elif r_share > 0.35:
                    d_seats = max(seats - 1, int(seats * 0.75))
                    r_seats = seats - d_seats
                else:
                    d_seats = seats
                    r_seats = 0
                unallocated = 0
        
        results.append({
            'district': district_key,
            'seats': seats,
            'r_strength': district_r_strength,
            'd_strength': district_d_strength,
            'r_share': r_share if total_strength > 0 else 0,
            'r_seats': r_seats,
            'd_seats': d_seats,
            'unallocated': unallocated,
            'towns_found': towns_found,
            'towns_total': len(towns)
        })
    
    return pd.DataFrame(results)

# Analyze each year
all_results = {}

for year in [2016, 2018, 2020]:
    print(f"\n\nAnalyzing {year}")
    print("-"*60)
    
    # Get town-level vote totals
    town_votes = get_town_vote_totals(year)
    
    # Map to current districts
    results = analyze_with_town_totals(year, town_votes)
    
    # Summary
    total_r = results['r_seats'].sum()
    total_d = results['d_seats'].sum()
    total_unalloc = results['unallocated'].sum()
    
    print(f"\nPredicted results in current districts: {total_r}R, {total_d}D")
    
    if total_unalloc > 0:
        print(f"Unallocated seats: {total_unalloc}")
    
    # Compare to actual
    actual = {2016: (226, 174), 2018: (167, 233), 2020: (213, 187)}
    actual_r, actual_d = actual[year]
    print(f"Actual results: {actual_r}R, {actual_d}D")
    print(f"Difference: {total_r - actual_r:+d}R, {total_d - actual_d:+d}D")
    
    # Save results
    results.to_csv(f'{year}_redistricting_corrected.csv', index=False)
    all_results[year] = results

# Final summary
print("\n\nFINAL ANALYSIS")
print("="*80)
print("Impact of current districts on historical elections:")
print("\nYear  Current Districts  Historical Results  Difference")
print("      R    D            R    D              R    D")
print("-"*60)

total_diff = 0
for year in [2016, 2018, 2020]:
    pred_r = all_results[year]['r_seats'].sum()
    pred_d = all_results[year]['d_seats'].sum()
    actual = {2016: (226, 174), 2018: (167, 233), 2020: (213, 187)}
    act_r, act_d = actual[year]
    diff_r = pred_r - act_r
    diff_d = pred_d - act_d
    
    print(f"{year}  {pred_r:3d}  {pred_d:3d}          {act_r:3d}  {act_d:3d}          {diff_r:+4d} {diff_d:+4d}")
    total_diff += diff_r

avg_diff = total_diff / 3
print(f"\nAverage impact: {avg_diff:+.1f} seats for Republicans")

if avg_diff > 0:
    print(f"\nThe current districts would have given Republicans an average of {avg_diff:.1f} more seats")
else:
    print(f"\nThe current districts would have given Republicans an average of {-avg_diff:.1f} fewer seats")
    
# Additional analysis
print("\n\nDETAILED BREAKDOWN")
print("="*80)

# Look at which types of districts changed
for year in [2016, 2018, 2020]:
    df = all_results[year]
    
    print(f"\n{year} District Analysis:")
    
    # Districts R would have won in current map but didn't in historical
    r_gains = df[(df['r_seats'] > df['d_seats']) & (df['r_share'] > 0.5)]
    print(f"  Districts where R won in current map: {len(r_gains)}")
    print(f"  Total R seats in those districts: {r_gains['r_seats'].sum()}")
    
    # Safe R districts (>58% R share)
    safe_r = df[df['r_share'] > 0.58]
    print(f"  Safe R districts (>58%): {len(safe_r)}")
    print(f"  Seats in safe R districts: {safe_r['seats'].sum()}")
    
    # Safe D districts (<42% R share)
    safe_d = df[df['r_share'] < 0.42]
    print(f"  Safe D districts (<42% R): {len(safe_d)}")
    print(f"  Seats in safe D districts: {safe_d['seats'].sum()}")
    
    # Competitive districts
    competitive = df[(df['r_share'] >= 0.42) & (df['r_share'] <= 0.58)]
    print(f"  Competitive districts: {len(competitive)}")
    print(f"  Seats in competitive districts: {competitive['seats'].sum()}")