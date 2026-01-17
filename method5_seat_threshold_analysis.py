#!/usr/bin/env python3
"""
Method 5: Analyze seat allocation thresholds in multi-member districts
How vote shares translate to seat wins
"""

import pandas as pd
import json
import numpy as np
from collections import defaultdict

print("METHOD 5: SEAT THRESHOLD ANALYSIS")
print("="*80)

# Load current results to calibrate thresholds
df_2022 = pd.read_csv('2022_nh_all_results_comprehensive.csv')
df_2024 = pd.read_csv('2024_nh_all_results_comprehensive.csv')
winners_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
winners_2024 = pd.read_csv('2024_nh_winners_comprehensive.csv')

# Analyze multi-member districts
print("\nANALYZING MULTI-MEMBER DISTRICT THRESHOLDS")
print("-"*60)

threshold_data = []

for year, df, winners in [(2022, df_2022, winners_2022), (2024, df_2024, winners_2024)]:
    # Get vote shares and seat outcomes by district
    for county in df['county'].unique():
        for district in df[df['county'] == county]['district'].unique():
            dist_data = df[(df['county'] == county) & (df['district'] == district)]
            dist_winners = winners[(winners['county'] == county) & (winners['district'] == district)]
            
            total_seats = len(dist_winners)
            if total_seats > 1:  # Multi-member only
                r_votes = dist_data[dist_data['party'] == 'R']['votes'].sum()
                d_votes = dist_data[dist_data['party'] == 'D']['votes'].sum()
                
                if r_votes + d_votes > 0:
                    r_share = r_votes / (r_votes + d_votes)
                    r_seats = len(dist_winners[dist_winners['party'] == 'R'])
                    d_seats = len(dist_winners[dist_winners['party'] == 'D'])
                    
                    threshold_data.append({
                        'year': year,
                        'district': f"{county}-{district}",
                        'total_seats': total_seats,
                        'r_vote_share': r_share,
                        'r_seat_share': r_seats / total_seats,
                        'r_seats': r_seats,
                        'd_seats': d_seats
                    })

# Analyze by seat count
for seats in [2, 3, 4]:
    subset = [d for d in threshold_data if d['total_seats'] == seats]
    if len(subset) >= 5:
        print(f"\n{seats}-member districts ({len(subset)} cases):")
        
        # Find vote share needed for different outcomes
        sweeps = [d for d in subset if d['r_seat_share'] == 1.0]
        majorities = [d for d in subset if d['r_seat_share'] > 0.5]
        splits = [d for d in subset if 0.4 <= d['r_seat_share'] <= 0.6]
        
        if sweeps:
            min_sweep = min(d['r_vote_share'] for d in sweeps)
            print(f"  Minimum R vote share for sweep: {min_sweep:.1%}")
        
        if majorities:
            min_majority = min(d['r_vote_share'] for d in majorities)
            print(f"  Minimum R vote share for majority: {min_majority:.1%}")
        
        # Average seat bonus for majority party
        majority_party_data = [d for d in subset if d['r_vote_share'] > 0.5]
        if majority_party_data:
            avg_bonus = np.mean([d['r_seat_share'] - d['r_vote_share'] for d in majority_party_data])
            print(f"  Average majority party seat bonus: {avg_bonus:.1%}")

# Now apply these thresholds to historical data
print("\n\nAPPLYING THRESHOLDS TO HISTORICAL DATA")
print("-"*60)

current_districts = json.load(open('current_district_structure.json'))

# Get seat counts
seats_map = {}
for t in threshold_data:
    seats_map[t['district']] = t['total_seats']

# Apply to historical years
for year in [2016, 2018, 2020]:
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    
    total_r_seats = 0
    total_d_seats = 0
    
    for dist_key, towns in current_districts.items():
        seats = seats_map.get(dist_key, 1)
        
        # Get votes
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
            
            # Apply calibrated thresholds
            if seats == 1:
                r_seats = 1 if r_share > 0.5 else 0
                d_seats = 1 - r_seats
            elif seats == 2:
                if r_share > 0.65:  # Threshold for 2-0
                    r_seats, d_seats = 2, 0
                elif r_share < 0.35:  # Threshold for 0-2
                    r_seats, d_seats = 0, 2
                else:  # Split
                    r_seats, d_seats = 1, 1
            elif seats == 3:
                if r_share > 0.62:  # Threshold for 3-0
                    r_seats, d_seats = 3, 0
                elif r_share > 0.54:  # Threshold for 2-1
                    r_seats, d_seats = 2, 1
                elif r_share > 0.46:  # Split possibilities
                    if r_share > 0.5:
                        r_seats, d_seats = 2, 1
                    else:
                        r_seats, d_seats = 1, 2
                elif r_share > 0.38:  # Threshold for 1-2
                    r_seats, d_seats = 1, 2
                else:  # Threshold for 0-3
                    r_seats, d_seats = 0, 3
            else:  # 4+ seats
                # Use proportional with majority bonus
                expected_r = seats * r_share
                if r_share > 0.55:
                    r_seats = max(int(expected_r + 0.5), int(seats * 0.6))
                elif r_share < 0.45:
                    d_expected = seats * (1 - r_share)
                    d_seats = max(int(d_expected + 0.5), int(seats * 0.6))
                    r_seats = seats - d_seats
                else:
                    r_seats = int(expected_r + 0.5)
                    d_seats = seats - r_seats
            
            total_r_seats += r_seats
            total_d_seats += d_seats
    
    print(f"\n{year}: {total_r_seats}R, {total_d_seats}D")
    
    # Compare to actual
    winners = json.load(open(f'nh_election_data/{year}_winners.json'))
    actual_r = sum(1 for d in winners.values() for w in d['winners'] if w['party'] == 'R')
    actual_d = sum(1 for d in winners.values() for w in d['winners'] if w['party'] == 'D')
    
    print(f"Actual: {actual_r}R, {actual_d}D")
    print(f"Difference: {total_r_seats - actual_r:+d}R")