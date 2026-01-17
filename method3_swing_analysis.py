#!/usr/bin/env python3
"""
Method 3: Analyze swing patterns and marginal districts
"""

import pandas as pd
import json
import numpy as np

print("METHOD 3: SWING DISTRICT ANALYSIS")
print("="*80)

# Load data
current_districts = json.load(open('current_district_structure.json'))

# Calculate swing patterns between elections
historical_results = {}
for year in [2016, 2018, 2020]:
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    historical_results[year] = df

# For each current district, calculate its swing pattern
district_swings = {}

for dist_key, towns in current_districts.items():
    swings = []
    
    # Calculate R share for each year
    for year in [2016, 2018, 2020]:
        df = historical_results[year]
        
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
            swings.append(r_share)
    
    if len(swings) >= 2:
        district_swings[dist_key] = {
            '2016': swings[0] if len(swings) > 0 else None,
            '2018': swings[1] if len(swings) > 1 else None,
            '2020': swings[2] if len(swings) > 2 else None,
            'volatility': np.std(swings),
            'avg': np.mean(swings)
        }

# Categorize districts by volatility
high_swing = [k for k, v in district_swings.items() if v['volatility'] > 0.05]
medium_swing = [k for k, v in district_swings.items() if 0.02 < v['volatility'] <= 0.05]
low_swing = [k for k, v in district_swings.items() if v['volatility'] <= 0.02]

print(f"\nDistrict volatility:")
print(f"  High swing (>5%): {len(high_swing)} districts")
print(f"  Medium swing (2-5%): {len(medium_swing)} districts")
print(f"  Low swing (<2%): {len(low_swing)} districts")

# Identify marginal districts in each year
for year in [2016, 2018, 2020]:
    print(f"\n{year} Marginal Districts (45-55% R):")
    
    marginal = []
    for dist, data in district_swings.items():
        if data.get(str(year)):
            r_share = data[str(year)]
            if 0.45 <= r_share <= 0.55:
                marginal.append((dist, r_share))
    
    marginal.sort(key=lambda x: x[1])
    print(f"  Found {len(marginal)} marginal districts")
    
    if marginal:
        print("  Top 5 most competitive:")
        for dist, share in marginal[:5]:
            print(f"    {dist}: {share:.1%} R")

# Calculate tipping point districts
print("\n\nTIPPING POINT ANALYSIS")
print("-"*60)

# Get seats from 2022 data
seats_df = pd.read_csv('2022_nh_winners_comprehensive.csv')
district_seats = {}
for county in seats_df['county'].unique():
    for district in seats_df[seats_df['county'] == county]['district'].unique():
        key = f"{county}-{district}"
        district_seats[key] = len(seats_df[(seats_df['county'] == county) & (seats_df['district'] == district)])

# For each year, find the tipping point
for year in [2016, 2018, 2020]:
    districts_ranked = []
    
    for dist, data in district_swings.items():
        if data.get(str(year)):
            r_share = data[str(year)]
            seats = district_seats.get(dist, 1)
            districts_ranked.append((dist, r_share, seats))
    
    # Sort by R share
    districts_ranked.sort(key=lambda x: x[1], reverse=True)
    
    # Find tipping point (200th and 201st seats)
    cumulative_seats = 0
    tipping_districts = []
    
    for dist, r_share, seats in districts_ranked:
        if cumulative_seats < 201:
            if cumulative_seats + seats >= 200:
                tipping_districts.append((dist, r_share, seats))
        cumulative_seats += seats
        
        if cumulative_seats >= 201 and len(tipping_districts) >= 2:
            break
    
    print(f"\n{year} Tipping point districts (around 200-201st seat):")
    for dist, share, seats in tipping_districts[:3]:
        print(f"  {dist}: {share:.1%} R ({seats} seats)")