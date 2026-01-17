#!/usr/bin/env python3
"""
Method 1: Track actual winners by town and see where they would have won
"""

import pandas as pd
import json
from collections import defaultdict

print("METHOD 1: WINNER TRACKING ANALYSIS")
print("="*80)

# Load data
current_districts = json.load(open('current_district_structure.json'))

# Load historical winners and track them by town
for year in [2016, 2018, 2020]:
    print(f"\n{year} WINNER TRACKING")
    print("-"*60)
    
    # Load winners and results
    winners = json.load(open(f'nh_election_data/{year}_winners.json'))
    results = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    
    # Map winners to their home towns (based on where they got votes)
    winner_towns = defaultdict(list)
    
    for district, info in winners.items():
        for winner in info['winners']:
            # Find this winner's strongest town
            candidate = winner.get('candidate', '')
            party = winner['party']
            
            if candidate:
                # Find where this candidate ran
                cand_results = results[results['candidate'] == candidate]
                if not cand_results.empty:
                    # Get their best town by vote count
                    best_town_idx = cand_results['votes'].idxmax()
                    best_town = cand_results.loc[best_town_idx, 'town']
                    winner_towns[best_town].append({
                        'candidate': candidate,
                        'party': party,
                        'historical_district': district
                    })
    
    # Now map these winners to current districts
    current_r = 0
    current_d = 0
    
    for dist_key, towns in current_districts.items():
        # Count winners from towns in this district
        r_winners = 0
        d_winners = 0
        
        for town in towns:
            if town in winner_towns:
                for winner in winner_towns[town]:
                    if winner['party'] == 'R':
                        r_winners += 1
                    elif winner['party'] == 'D':
                        d_winners += 1
        
        # Allocate based on winner counts
        # This is rough but gives us a sense
        if r_winners > d_winners:
            current_r += 1
        elif d_winners > r_winners:
            current_d += 1
    
    print(f"Winners tracked to current districts: {current_r}R, {current_d}D")
    
    # Compare to actual
    actual_r = sum(1 for d in winners.values() for w in d['winners'] if w['party'] == 'R')
    actual_d = sum(1 for d in winners.values() for w in d['winners'] if w['party'] == 'D')
    print(f"Actual historical: {actual_r}R, {actual_d}D")
    print(f"Difference: {current_r - actual_r:+d}R")