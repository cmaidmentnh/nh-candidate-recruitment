#!/usr/bin/env python3
"""
Method 2: Analyze voting power concentration in current districts
"""

import pandas as pd
import json
import numpy as np

print("METHOD 2: VOTE POWER CONCENTRATION ANALYSIS")
print("="*80)

# Load data
current_districts = json.load(open('current_district_structure.json'))

# For each historical year, calculate vote concentration
for year in [2016, 2018, 2020]:
    print(f"\n{year} VOTE POWER ANALYSIS")
    print("-"*60)
    
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    
    # Calculate total votes by party statewide
    total_r = df[df['party'] == 'R']['votes'].sum()
    total_d = df[df['party'] == 'D']['votes'].sum()
    
    print(f"Statewide: R {total_r:,} ({total_r/(total_r+total_d):.1%}), D {total_d:,}")
    
    # Now calculate concentration in current districts
    district_concentrations = []
    
    for dist_key, towns in current_districts.items():
        # Get votes from these towns
        dist_r = 0
        dist_d = 0
        
        for town in towns:
            town_data = df[df['town'] == town]
            if town_data.empty and ' Ward ' in town:
                town_data = df[df['town'] == town.replace(' Ward ', ' Wd ')]
            
            dist_r += town_data[town_data['party'] == 'R']['votes'].sum()
            dist_d += town_data[town_data['party'] == 'D']['votes'].sum()
        
        if dist_r + dist_d > 0:
            r_share = dist_r / (dist_r + dist_d)
            
            # Calculate concentration index (how far from 50-50)
            concentration = abs(r_share - 0.5) * 2
            
            district_concentrations.append({
                'district': dist_key,
                'r_votes': dist_r,
                'd_votes': dist_d,
                'r_share': r_share,
                'concentration': concentration,
                'winner': 'R' if r_share > 0.5 else 'D'
            })
    
    # Analyze packing
    r_districts = [d for d in district_concentrations if d['winner'] == 'R']
    d_districts = [d for d in district_concentrations if d['winner'] == 'D']
    
    avg_r_concentration = np.mean([d['concentration'] for d in r_districts]) if r_districts else 0
    avg_d_concentration = np.mean([d['concentration'] for d in d_districts]) if d_districts else 0
    
    print(f"\nDistrict winners: {len(r_districts)}R, {len(d_districts)}D")
    print(f"Average concentration:")
    print(f"  R districts: {avg_r_concentration:.3f} (higher = more packed)")
    print(f"  D districts: {avg_d_concentration:.3f}")
    
    # Calculate wasted votes
    r_wasted = sum(d['r_votes'] for d in d_districts)  # R votes in D districts
    d_wasted = sum(d['d_votes'] for d in r_districts)  # D votes in R districts
    
    # Add surplus votes in won districts
    for d in r_districts:
        needed = (d['r_votes'] + d['d_votes']) * 0.5 + 1
        r_wasted += max(0, d['r_votes'] - needed)
    
    for d in d_districts:
        needed = (d['r_votes'] + d['d_votes']) * 0.5 + 1
        d_wasted += max(0, d['d_votes'] - needed)
    
    print(f"\nWasted votes:")
    print(f"  R: {r_wasted:,} ({r_wasted/total_r:.1%} of total)")
    print(f"  D: {d_wasted:,} ({d_wasted/total_d:.1%} of total)")
    
    efficiency_gap = (r_wasted - d_wasted) / (total_r + total_d)
    print(f"  Efficiency gap: {efficiency_gap:+.3f} ({'R' if efficiency_gap < 0 else 'D'} advantage)")