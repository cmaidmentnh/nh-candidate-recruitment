#!/usr/bin/env python3
"""
Accurately predict historical seat allocations in current districts
Using town-level vote shares and proper seat allocation rules
"""

import pandas as pd
import json
import numpy as np
from collections import defaultdict

# Load current district structure
with open('current_district_structure.json', 'r') as f:
    current_districts = json.load(f)

# Load comprehensive data with vote totals
df = pd.read_csv('comprehensive_district_town_data.csv')

def calculate_district_results(year):
    """Calculate results for a given year"""
    
    year_df = df[df['year'] == year]
    
    results = []
    
    for district_key, towns in current_districts.items():
        county, district_num = district_key.split('-')
        district_num = int(district_num)
        
        # Get data for this district
        district_data = year_df[
            (year_df['county'] == county) & 
            (year_df['districtNum'] == district_num)
        ]
        
        if district_data.empty:
            continue
            
        # Get seat count from any row (should be same for all)
        seats = district_data['seats'].iloc[0]
        
        # Aggregate votes across all towns in district
        total_r = district_data['total_R'].sum()
        total_d = district_data['total_D'].sum()
        total_other = district_data['total_Other'].sum()
        
        # Get unique candidate counts (max across towns since candidates run district-wide)
        r_candidates = district_data['R_candidate_count'].max()
        d_candidates = district_data['D_candidate_count'].max()
        
        # Calculate vote share
        total_major = total_r + total_d
        if total_major == 0:
            r_share = 0.5  # No data, split evenly
        else:
            r_share = total_r / total_major
        
        # Allocate seats based on rules
        r_seats = 0
        d_seats = 0
        
        if r_candidates == 0 and d_candidates == 0:
            # No candidates - unallocated
            pass
        elif r_candidates == 0:
            # Only D candidates
            d_seats = min(d_candidates, seats)
        elif d_candidates == 0:
            # Only R candidates  
            r_seats = min(r_candidates, seats)
        elif r_candidates + d_candidates <= seats:
            # Not enough candidates to fill all seats
            r_seats = r_candidates
            d_seats = d_candidates
        else:
            # Competitive race - allocate based on vote share
            if seats == 1:
                # Single member - winner take all
                if r_share > 0.5:
                    r_seats = 1
                else:
                    d_seats = 1
            else:
                # Multi-member district
                # Use historical patterns: majority party typically gets a bonus
                # Base allocation on vote share, then apply realistic adjustments
                
                if r_share > 0.65:
                    # Strong R - likely sweep or near sweep
                    r_seats = min(seats, int(seats * 0.9 + 0.5))
                    d_seats = seats - r_seats
                elif r_share > 0.55:
                    # Clear R majority - gets majority bonus
                    r_seats = max(int(seats * r_share + 0.5), int(seats * 0.6 + 0.5))
                    r_seats = min(r_seats, seats)
                    d_seats = seats - r_seats
                elif r_share > 0.45:
                    # Competitive - close to proportional
                    r_seats = int(seats * r_share + 0.5)
                    d_seats = seats - r_seats
                elif r_share > 0.35:
                    # Clear D majority
                    d_seats = max(int(seats * (1-r_share) + 0.5), int(seats * 0.6 + 0.5))
                    d_seats = min(d_seats, seats)
                    r_seats = seats - d_seats
                else:
                    # Strong D
                    d_seats = min(seats, int(seats * 0.9 + 0.5))
                    r_seats = seats - d_seats
        
        results.append({
            'county': county,
            'district': district_num,
            'seats': seats,
            'r_votes': total_r,
            'd_votes': total_d,
            'r_share': r_share,
            'r_candidates': r_candidates,
            'd_candidates': d_candidates,
            'r_seats': r_seats,
            'd_seats': d_seats,
            'unallocated': seats - r_seats - d_seats
        })
    
    return pd.DataFrame(results)

# Calculate for each year
print("PREDICTED SEAT ALLOCATIONS IN CURRENT DISTRICTS")
print("="*80)

summary = []

for year in [2016, 2018, 2020]:
    results = calculate_district_results(year)
    
    total_r = results['r_seats'].sum()
    total_d = results['d_seats'].sum()
    total_unalloc = results['unallocated'].sum()
    total_seats = results['seats'].sum()
    
    # Get actual results for comparison
    actual_results = {
        2016: {'R': 226, 'D': 174},
        2018: {'R': 167, 'D': 233},
        2020: {'R': 213, 'D': 187}
    }
    
    actual_r = actual_results[year]['R']
    actual_d = actual_results[year]['D']
    
    print(f"\n{year}:")
    print(f"  Predicted in current districts: {total_r}R, {total_d}D (unallocated: {total_unalloc})")
    print(f"  Actual results: {actual_r}R, {actual_d}D")
    print(f"  Difference: {total_r - actual_r:+d}R, {total_d - actual_d:+d}D")
    
    # Save detailed results
    results.to_csv(f'{year}_predicted_in_current_districts.csv', index=False)
    
    summary.append({
        'year': year,
        'predicted_r': total_r,
        'predicted_d': total_d,
        'unallocated': total_unalloc,
        'total_seats': total_seats,
        'actual_r': actual_r,
        'actual_d': actual_d,
        'r_difference': total_r - actual_r,
        'd_difference': total_d - actual_d
    })

# Save summary
summary_df = pd.DataFrame(summary)
summary_df.to_csv('historical_predictions_summary.csv', index=False)

print("\n\nSUMMARY")
print("="*80)
print("If historical elections had been run in current districts:")
print(f"2016: {summary[0]['predicted_r']}R-{summary[0]['predicted_d']}D (vs actual {summary[0]['actual_r']}R-{summary[0]['actual_d']}D)")
print(f"2018: {summary[1]['predicted_r']}R-{summary[1]['predicted_d']}D (vs actual {summary[1]['actual_r']}R-{summary[1]['actual_d']}D)")
print(f"2020: {summary[2]['predicted_r']}R-{summary[2]['predicted_d']}D (vs actual {summary[2]['actual_r']}R-{summary[2]['actual_d']}D)")

avg_r_diff = np.mean([s['r_difference'] for s in summary])
print(f"\nAverage R difference: {avg_r_diff:+.1f} seats")
print(f"This suggests the current districts give Republicans a {-avg_r_diff:+.1f} seat disadvantage compared to the old districts")