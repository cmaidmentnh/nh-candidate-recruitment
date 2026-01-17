#!/usr/bin/env python3
"""
Method 6: Calculate packing and cracking indices
Compare concentration of party voters
"""

import pandas as pd
import json
import numpy as np
from scipy import stats

print("METHOD 6: PACKING AND CRACKING INDEX")
print("="*80)

# Load data
current_districts = json.load(open('current_district_structure.json'))

# For each year, calculate packing/cracking metrics
for year in [2016, 2018, 2020]:
    print(f"\n{year} PACKING/CRACKING ANALYSIS")
    print("-"*60)
    
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    
    # Calculate district-level metrics
    r_percentages = []
    d_percentages = []
    district_data = []
    
    for dist_key, towns in current_districts.items():
        dist_r = 0
        dist_d = 0
        
        for town in towns:
            town_data = df[df['town'] == town]
            if town_data.empty and ' Ward ' in town:
                town_data = df[df['town'] == town.replace(' Ward ', ' Wd ')]
            
            dist_r += town_data[town_data['party'] == 'R']['votes'].sum()
            dist_d += town_data[town_data['party'] == 'D']['votes'].sum()
        
        total = dist_r + dist_d
        if total > 0:
            r_pct = dist_r / total
            d_pct = dist_d / total
            
            district_data.append({
                'district': dist_key,
                'r_votes': dist_r,
                'd_votes': dist_d,
                'r_pct': r_pct,
                'd_pct': d_pct,
                'total': total,
                'winner': 'R' if r_pct > 0.5 else 'D'
            })
            
            r_percentages.append(r_pct)
            d_percentages.append(d_pct)
    
    # Calculate packing metrics
    r_districts = [d for d in district_data if d['winner'] == 'R']
    d_districts = [d for d in district_data if d['winner'] == 'D']
    
    # Average margin in won districts (higher = more packed)
    r_margins = [d['r_pct'] - 0.5 for d in r_districts]
    d_margins = [d['d_pct'] - 0.5 for d in d_districts]
    
    avg_r_margin = np.mean(r_margins) if r_margins else 0
    avg_d_margin = np.mean(d_margins) if d_margins else 0
    
    print(f"\nAverage winning margins:")
    print(f"  R districts: {avg_r_margin:.1%} (won {len(r_districts)} districts)")
    print(f"  D districts: {avg_d_margin:.1%} (won {len(d_districts)} districts)")
    
    # Gini coefficient (inequality of vote distribution)
    # Simple Gini calculation
    def gini(x):
        # Sort values
        sorted_x = sorted(x)
        n = len(x)
        cumsum = np.cumsum(sorted_x)
        return (2 * np.sum((np.arange(1, n+1) * sorted_x))) / (n * np.sum(x)) - (n + 1) / n
    
    r_gini = gini(r_percentages) if len(r_percentages) > 0 else 0
    d_gini = gini([1-r for r in r_percentages]) if len(r_percentages) > 0 else 0
    
    print(f"\nGini coefficients (0=equal, 1=concentrated):")
    print(f"  R vote concentration: {r_gini:.3f}")
    print(f"  D vote concentration: {d_gini:.3f}")
    
    # Find most packed districts
    r_packed = sorted(r_districts, key=lambda x: x['r_pct'], reverse=True)[:5]
    d_packed = sorted(d_districts, key=lambda x: x['d_pct'], reverse=True)[:5]
    
    print(f"\nMost packed R districts:")
    for d in r_packed:
        print(f"  {d['district']}: {d['r_pct']:.1%} R")
    
    print(f"\nMost packed D districts:")
    for d in d_packed:
        print(f"  {d['district']}: {d['d_pct']:.1%} D")
    
    # Cracking analysis - where is the losing party strongest?
    r_in_d_districts = sorted([d for d in d_districts], key=lambda x: x['r_pct'], reverse=True)[:5]
    d_in_r_districts = sorted([d for d in r_districts], key=lambda x: x['d_pct'], reverse=True)[:5]
    
    print(f"\nStrongest R performance in D districts (potential cracks):")
    for d in r_in_d_districts:
        print(f"  {d['district']}: {d['r_pct']:.1%} R (D won)")
    
    print(f"\nStrongest D performance in R districts (potential cracks):")
    for d in d_in_r_districts:
        print(f"  {d['district']}: {d['d_pct']:.1%} D (R won)")

# Calculate overall packing efficiency
print("\n\nOVERALL PACKING EFFICIENCY SCORE")
print("="*80)

# Compare to optimal distribution
# In optimal gerrymandering, winning party wins by small margins, losing party is highly packed
# Score: (opponent avg margin - your avg margin) * seats won / total seats

scores = {}
for year in [2016, 2018, 2020]:
    # Recalculate for this summary
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    
    district_results = []
    for dist_key, towns in current_districts.items():
        dist_r = 0
        dist_d = 0
        
        for town in towns:
            town_data = df[df['town'] == town]
            if town_data.empty and ' Ward ' in town:
                town_data = df[df['town'] == town.replace(' Ward ', ' Wd ')]
            
            dist_r += town_data[town_data['party'] == 'R']['votes'].sum()
            dist_d += town_data[town_data['party'] == 'D']['votes'].sum()
        
        if dist_r + dist_d > 0:
            r_pct = dist_r / (dist_r + dist_d)
            winner = 'R' if r_pct > 0.5 else 'D'
            margin = abs(r_pct - 0.5)
            
            district_results.append({
                'winner': winner,
                'margin': margin,
                'r_pct': r_pct
            })
    
    r_won = [d for d in district_results if d['winner'] == 'R']
    d_won = [d for d in district_results if d['winner'] == 'D']
    
    r_avg_margin = np.mean([d['margin'] for d in r_won]) if r_won else 0
    d_avg_margin = np.mean([d['margin'] for d in d_won]) if d_won else 0
    
    # Efficiency score (positive = R advantage, negative = D advantage)
    r_efficiency = (d_avg_margin - r_avg_margin) * len(r_won) / len(district_results)
    
    scores[year] = {
        'r_efficiency': r_efficiency,
        'r_districts': len(r_won),
        'd_districts': len(d_won),
        'r_margin': r_avg_margin,
        'd_margin': d_avg_margin
    }

print("\nPacking efficiency scores (positive = R advantage):")
for year, score in scores.items():
    print(f"{year}: {score['r_efficiency']:+.3f} "
          f"(R won {score['r_districts']} with {score['r_margin']:.1%} avg margin, "
          f"D won {score['d_districts']} with {score['d_margin']:.1%} avg margin)")