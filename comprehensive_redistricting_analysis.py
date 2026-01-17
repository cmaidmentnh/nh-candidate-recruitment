#!/usr/bin/env python3
"""
Comprehensive analysis of how current districts would have performed historically
This should show Republican advantage in current maps
"""

import pandas as pd
import json
import numpy as np
from collections import defaultdict

# Load current district structure
with open('current_district_structure.json', 'r') as f:
    current_districts = json.load(f)

# First, let's understand what we're working with
print("COMPREHENSIVE REDISTRICTING ANALYSIS")
print("="*80)
print(f"Total current districts: {len(current_districts)}")

# Count seats by examining 2022/2024 data
seats_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
district_seats = {}
for county in seats_2022['county'].unique():
    for district in seats_2022[seats_2022['county'] == county]['district'].unique():
        key = f"{county}-{district}"
        count = len(seats_2022[(seats_2022['county'] == county) & (seats_2022['district'] == district)])
        district_seats[key] = count

total_seats = sum(district_seats.values())
print(f"Total seats: {total_seats}")

# Load all historical data
historical_data = {}
for year in [2016, 2018, 2020]:
    historical_data[year] = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')

# For each current district, calculate what would have happened
def analyze_district_historical(district_key, towns, year_data):
    """Analyze how a current district would have performed with historical data"""
    
    # Aggregate votes from all towns in this district
    r_votes = 0
    d_votes = 0
    other_votes = 0
    r_candidates = set()
    d_candidates = set()
    towns_found = 0
    
    for town in towns:
        # Find this town in historical data
        town_data = year_data[year_data['town'] == town]
        
        if town_data.empty and ' Ward ' in town:
            # Try alternate format
            alt_town = town.replace(' Ward ', ' Wd ')
            town_data = year_data[year_data['town'] == alt_town]
        
        if not town_data.empty:
            towns_found += 1
            
            # Sum votes by party
            for _, row in town_data.iterrows():
                if row['party'] == 'R':
                    r_votes += row['votes']
                    r_candidates.add(row['candidate'])
                elif row['party'] == 'D':
                    d_votes += row['votes']
                    d_candidates.add(row['candidate'])
                else:
                    other_votes += row['votes']
    
    return {
        'r_votes': r_votes,
        'd_votes': d_votes,
        'other_votes': other_votes,
        'r_candidates': len(r_candidates),
        'd_candidates': len(d_candidates),
        'towns_found': towns_found,
        'towns_total': len(towns)
    }

# Analyze each year
results_by_year = {}

for year in [2016, 2018, 2020]:
    print(f"\n\nAnalyzing {year}")
    print("-"*60)
    
    year_data = historical_data[year]
    district_results = []
    
    # Check total votes in source data
    total_r_source = year_data[year_data['party'] == 'R']['votes'].sum()
    total_d_source = year_data[year_data['party'] == 'D']['votes'].sum()
    print(f"Source data total votes - R: {total_r_source:,}, D: {total_d_source:,}")
    
    for district_key, towns in current_districts.items():
        seats = district_seats.get(district_key, 1)
        
        # Analyze this district
        analysis = analyze_district_historical(district_key, towns, year_data)
        
        # Determine seat allocation
        total_votes = analysis['r_votes'] + analysis['d_votes']
        
        if total_votes == 0:
            # No data for this district
            r_seats = 0
            d_seats = 0
            unallocated = seats
        else:
            r_share = analysis['r_votes'] / total_votes
            
            # Apply seat allocation rules
            if seats == 1:
                # Single member - winner take all
                if r_share > 0.5:
                    r_seats = 1
                    d_seats = 0
                else:
                    r_seats = 0
                    d_seats = 1
                unallocated = 0
            else:
                # Multi-member districts
                # NH House uses plurality-at-large: voters vote for N candidates, top N win
                # This creates a winner-take-all tendency in polarized districts
                
                if r_share > 0.60:
                    # Strong R district - Rs likely win all or nearly all seats
                    if r_share > 0.65:
                        r_seats = seats  # Clean sweep
                        d_seats = 0
                    else:
                        r_seats = max(seats - 1, int(seats * 0.8))
                        d_seats = seats - r_seats
                elif r_share > 0.52:
                    # Lean R - Rs get majority but not all
                    r_seats = max(int(seats * 0.6), int(seats * r_share + 1))
                    r_seats = min(r_seats, seats)
                    d_seats = seats - r_seats
                elif r_share > 0.48:
                    # True tossup - split close to even
                    r_seats = int(seats * r_share + 0.5)
                    d_seats = seats - r_seats
                elif r_share > 0.40:
                    # Lean D
                    d_seats = max(int(seats * 0.6), int(seats * (1-r_share) + 1))
                    d_seats = min(d_seats, seats)
                    r_seats = seats - d_seats
                else:
                    # Strong D
                    if r_share < 0.35:
                        d_seats = seats  # Clean sweep
                        r_seats = 0
                    else:
                        d_seats = max(seats - 1, int(seats * 0.8))
                        r_seats = seats - d_seats
                unallocated = 0
        
        district_results.append({
            'district': district_key,
            'seats': seats,
            'r_votes': analysis['r_votes'],
            'd_votes': analysis['d_votes'],
            'r_share': r_share if total_votes > 0 else 0,
            'r_seats': r_seats,
            'd_seats': d_seats,
            'unallocated': unallocated,
            'towns_found': analysis['towns_found'],
            'towns_total': analysis['towns_total']
        })
    
    # Convert to DataFrame for analysis
    df_results = pd.DataFrame(district_results)
    
    # Summary statistics
    total_r_seats = df_results['r_seats'].sum()
    total_d_seats = df_results['d_seats'].sum()
    total_unalloc = df_results['unallocated'].sum()
    
    # Vote totals in our mapping
    total_r_mapped = df_results['r_votes'].sum()
    total_d_mapped = df_results['d_votes'].sum()
    
    print(f"\nVotes captured in mapping - R: {total_r_mapped:,}, D: {total_d_mapped:,}")
    print(f"Percentage captured - R: {(total_r_mapped/total_r_source)*100:.1f}%, D: {(total_d_mapped/total_d_source)*100:.1f}%")
    
    print(f"\nSeat allocation in current districts:")
    print(f"R: {total_r_seats}, D: {total_d_seats}, Unallocated: {total_unalloc}")
    
    # Compare to actual
    actual = {2016: (226, 174), 2018: (167, 233), 2020: (213, 187)}
    actual_r, actual_d = actual[year]
    print(f"\nActual results in old districts: R: {actual_r}, D: {actual_d}")
    print(f"Difference: R {total_r_seats - actual_r:+d}, D {total_d_seats - actual_d:+d}")
    
    # Save detailed results
    df_results.to_csv(f'{year}_current_districts_detailed.csv', index=False)
    results_by_year[year] = df_results

# Final summary
print("\n\nFINAL SUMMARY")
print("="*80)
print("How current districts would have performed historically:")
print("Year  Predicted  Actual     Difference")
print("      R    D     R    D     R    D")
print("-"*40)

for year in [2016, 2018, 2020]:
    df = results_by_year[year]
    pred_r = df['r_seats'].sum()
    pred_d = df['d_seats'].sum()
    actual = {2016: (226, 174), 2018: (167, 233), 2020: (213, 187)}
    act_r, act_d = actual[year]
    
    print(f"{year}  {pred_r:3d}  {pred_d:3d}   {act_r:3d}  {act_d:3d}   {pred_r-act_r:+3d}  {pred_d-act_d:+3d}")

# Calculate average advantage
advantages = []
for year in [2016, 2018, 2020]:
    df = results_by_year[year]
    pred_r = df['r_seats'].sum()
    actual = {2016: (226, 174), 2018: (167, 233), 2020: (213, 187)}
    act_r, _ = actual[year]
    advantages.append(pred_r - act_r)

avg_advantage = np.mean(advantages)
print(f"\nAverage R seat difference in current districts: {avg_advantage:+.1f}")

if avg_advantage > 0:
    print(f"Current districts give Republicans a {avg_advantage:.1f} seat advantage")
else:
    print(f"Current districts give Republicans a {-avg_advantage:.1f} seat disadvantage")
    print("\nThis seems inconsistent with expectations. Let me check for issues...")
    
    # Diagnostic check
    print("\nDiagnostic information:")
    for year in [2016, 2018, 2020]:
        df = results_by_year[year]
        missing_towns = df[df['towns_found'] < df['towns_total']]
        if len(missing_towns) > 0:
            print(f"\n{year}: {len(missing_towns)} districts with missing town data")
            print(f"Total towns missing data: {(df['towns_total'] - df['towns_found']).sum()}")