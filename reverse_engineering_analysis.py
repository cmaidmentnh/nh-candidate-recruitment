#!/usr/bin/env python3
"""
Reverse engineering approach - start with known 2022/2024 results
and work backwards to understand historical performance
"""

import pandas as pd
import json
import numpy as np
from collections import defaultdict

print("REVERSE ENGINEERING REDISTRICTING ANALYSIS")
print("="*80)
print("Starting with known outcomes in current districts\n")

# Load current district structure and results
current_districts = json.load(open('current_district_structure.json'))

# Load 2022 and 2024 comprehensive results
df_2022 = pd.read_csv('2022_nh_all_results_comprehensive.csv')
df_2024 = pd.read_csv('2024_nh_all_results_comprehensive.csv')

# Load winner files
winners_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
winners_2024 = pd.read_csv('2024_nh_winners_comprehensive.csv')

print("STEP 1: ESTABLISH CURRENT DISTRICT BASELINE")
print("="*80)

# For each current district, get actual performance
current_performance = {}

for dist_key in current_districts.keys():
    county, dist_num = dist_key.split('-')
    dist_num = int(dist_num)
    
    # Get 2022 results
    dist_2022 = df_2022[(df_2022['county'] == county) & (df_2022['district'] == dist_num)]
    r_votes_2022 = dist_2022[dist_2022['party'] == 'R']['votes'].sum()
    d_votes_2022 = dist_2022[dist_2022['party'] == 'D']['votes'].sum()
    
    # Get 2022 winners
    winners_2022_dist = winners_2022[(winners_2022['county'] == county) & (winners_2022['district'] == dist_num)]
    r_seats_2022 = len(winners_2022_dist[winners_2022_dist['party'] == 'R'])
    d_seats_2022 = len(winners_2022_dist[winners_2022_dist['party'] == 'D'])
    
    # Get 2024 results
    dist_2024 = df_2024[(df_2024['county'] == county) & (df_2024['district'] == dist_num)]
    r_votes_2024 = dist_2024[dist_2024['party'] == 'R']['votes'].sum()
    d_votes_2024 = dist_2024[dist_2024['party'] == 'D']['votes'].sum()
    
    # Get 2024 winners
    winners_2024_dist = winners_2024[(winners_2024['county'] == county) & (winners_2024['district'] == dist_num)]
    r_seats_2024 = len(winners_2024_dist[winners_2024_dist['party'] == 'R'])
    d_seats_2024 = len(winners_2024_dist[winners_2024_dist['party'] == 'D'])
    
    total_seats = r_seats_2022 + d_seats_2022
    
    # Calculate metrics
    r_vote_share_2022 = r_votes_2022 / (r_votes_2022 + d_votes_2022) if (r_votes_2022 + d_votes_2022) > 0 else 0
    r_vote_share_2024 = r_votes_2024 / (r_votes_2024 + d_votes_2024) if (r_votes_2024 + d_votes_2024) > 0 else 0
    
    # Seat efficiency (seats won / expected seats based on votes)
    if total_seats > 0 and r_vote_share_2022 > 0:
        r_efficiency_2022 = (r_seats_2022 / total_seats) / r_vote_share_2022
    else:
        r_efficiency_2022 = 1.0
    
    current_performance[dist_key] = {
        'seats': total_seats,
        'r_vote_share_2022': r_vote_share_2022,
        'r_vote_share_2024': r_vote_share_2024,
        'r_seats_2022': r_seats_2022,
        'd_seats_2022': d_seats_2022,
        'r_seats_2024': r_seats_2024,
        'd_seats_2024': d_seats_2024,
        'r_efficiency': r_efficiency_2022,
        'avg_r_vote_share': (r_vote_share_2022 + r_vote_share_2024) / 2
    }

# Analyze patterns
print("\nCurrent district patterns:")

# Count safe districts
very_safe_r = sum(1 for d in current_performance.values() if d['avg_r_vote_share'] > 0.65)
safe_r = sum(1 for d in current_performance.values() if 0.58 < d['avg_r_vote_share'] <= 0.65)
lean_r = sum(1 for d in current_performance.values() if 0.52 < d['avg_r_vote_share'] <= 0.58)
tossup = sum(1 for d in current_performance.values() if 0.48 <= d['avg_r_vote_share'] <= 0.52)
lean_d = sum(1 for d in current_performance.values() if 0.42 <= d['avg_r_vote_share'] < 0.48)
safe_d = sum(1 for d in current_performance.values() if 0.35 <= d['avg_r_vote_share'] < 0.42)
very_safe_d = sum(1 for d in current_performance.values() if d['avg_r_vote_share'] < 0.35)

print(f"  Very Safe R (>65%): {very_safe_r}")
print(f"  Safe R (58-65%): {safe_r}")
print(f"  Lean R (52-58%): {lean_r}")
print(f"  Tossup (48-52%): {tossup}")
print(f"  Lean D (42-48%): {lean_d}")
print(f"  Safe D (35-42%): {safe_d}")
print(f"  Very Safe D (<35%): {very_safe_d}")

# Calculate seat efficiency
avg_r_efficiency = np.mean([d['r_efficiency'] for d in current_performance.values() if d['r_efficiency'] < 10])
print(f"\nAverage R seat efficiency in current districts: {avg_r_efficiency:.2f}")

print("\n" + "="*80)
print("STEP 2: CALCULATE ENVIRONMENTAL BASELINES")
print("="*80)

# Known results
results = {
    2016: {'R': 226, 'D': 174},
    2018: {'R': 167, 'D': 233},
    2020: {'R': 213, 'D': 187},
    2022: {'R': 201, 'D': 198},
    2024: {'R': 222, 'D': 178}
}

# Calculate environments relative to 50-50
environments = {}
for year, res in results.items():
    total = res['R'] + res['D']
    r_pct = res['R'] / total
    environments[year] = (r_pct - 0.5) * 100

print("\nElection environments (R+ advantage):")
for year, env in environments.items():
    print(f"  {year}: {env:+.1f}%")

# Current district baseline (average of 2022 and 2024)
baseline_env = (environments[2022] + environments[2024]) / 2
print(f"\nCurrent district baseline environment: {baseline_env:+.1f}%")

print("\n" + "="*80)
print("STEP 3: MODEL SEAT CHANGES WITH ENVIRONMENT")
print("="*80)

# Analyze how seats change with environment in current districts
# Use 2022 vs 2024 as calibration
env_change = environments[2024] - environments[2022]
seat_change = results[2024]['R'] - results[2022]['R']
seats_per_point = seat_change / env_change if env_change != 0 else 0

print(f"\nEnvironment sensitivity in current districts:")
print(f"  2022 to 2024 environment change: {env_change:+.1f}%")
print(f"  2022 to 2024 R seat change: {seat_change:+d}")
print(f"  Seats per environment point: {seats_per_point:.1f}")

# Identify swing districts
swing_districts = []
for dist_key, perf in current_performance.items():
    if perf['r_seats_2022'] != perf['r_seats_2024']:
        swing_districts.append({
            'district': dist_key,
            'seats': perf['seats'],
            '2022_result': f"{perf['r_seats_2022']}R-{perf['d_seats_2022']}D",
            '2024_result': f"{perf['r_seats_2024']}R-{perf['d_seats_2024']}D",
            'avg_r_share': perf['avg_r_vote_share']
        })

print(f"\nFound {len(swing_districts)} districts that changed hands between 2022 and 2024")

print("\n" + "="*80)
print("STEP 4: PROJECT HISTORICAL RESULTS")
print("="*80)

# Use the calibrated model to project backwards
def project_seats(base_r_seats, base_environment, target_environment, sensitivity):
    """Project seats based on environment change"""
    env_diff = target_environment - base_environment
    seat_change = env_diff * sensitivity
    return int(base_r_seats + seat_change + 0.5)

# Start from 2022-2024 average
base_r_seats = (results[2022]['R'] + results[2024]['R']) / 2
base_d_seats = (results[2022]['D'] + results[2024]['D']) / 2

print("\nProjected results in current districts:")
print("\nYear  Environment  Projected  Actual     Difference")
print("      vs baseline  R    D     R    D     R    D")
print("-"*60)

projections = {}
for year in [2016, 2018, 2020]:
    env_diff = environments[year] - baseline_env
    
    # Project seats
    proj_r = project_seats(base_r_seats, baseline_env, environments[year], seats_per_point)
    proj_d = 400 - proj_r
    
    projections[year] = {'R': proj_r, 'D': proj_d}
    
    # Compare to actual
    diff_r = proj_r - results[year]['R']
    diff_d = proj_d - results[year]['D']
    
    print(f"{year}  {env_diff:+5.1f}%      {proj_r:3d}  {proj_d:3d}   "
          f"{results[year]['R']:3d}  {results[year]['D']:3d}   "
          f"{diff_r:+4d} {diff_d:+4d}")

# Calculate average difference
avg_diff = np.mean([projections[y]['R'] - results[y]['R'] for y in [2016, 2018, 2020]])

print(f"\nAverage R seat difference: {avg_diff:+.1f}")

print("\n" + "="*80)
print("STEP 5: DETAILED DISTRICT-LEVEL ANALYSIS")
print("="*80)

# For more accurate projection, model each district individually
detailed_projections = {}

for year in [2016, 2018, 2020]:
    env_shift = environments[year] - baseline_env
    
    total_r = 0
    total_d = 0
    
    district_results = []
    
    for dist_key, perf in current_performance.items():
        seats = perf['seats']
        base_r_share = perf['avg_r_vote_share']
        
        # Adjust for environment
        # More competitive districts swing more
        competitiveness = 1 - abs(base_r_share - 0.5) * 2
        swing_factor = competitiveness * 0.5 + 0.5  # 50% to 100% of full swing
        
        adjusted_r_share = base_r_share + (env_shift / 100) * swing_factor
        adjusted_r_share = max(0.01, min(0.99, adjusted_r_share))
        
        # Allocate seats
        if seats == 1:
            if adjusted_r_share > 0.5:
                r_seats = 1
                d_seats = 0
            else:
                r_seats = 0
                d_seats = 1
        else:
            # Use efficiency model from current performance
            efficiency = perf['r_efficiency']
            
            # Expected seats based on vote share
            expected_r = seats * adjusted_r_share
            
            # Apply efficiency factor
            if adjusted_r_share > 0.5:
                # R majority - apply efficiency
                r_seats = min(seats, int(expected_r * efficiency + 0.5))
                d_seats = seats - r_seats
            else:
                # D majority - inverse efficiency
                d_expected = seats * (1 - adjusted_r_share)
                d_efficiency = 2 - efficiency  # Inverse of R efficiency
                d_seats = min(seats, int(d_expected * d_efficiency + 0.5))
                r_seats = seats - d_seats
        
        total_r += r_seats
        total_d += d_seats
        
        district_results.append({
            'district': dist_key,
            'r_seats': r_seats,
            'd_seats': d_seats
        })
    
    detailed_projections[year] = {
        'R': total_r,
        'D': total_d,
        'districts': district_results
    }

print("\nDetailed district-by-district projections:")
print("\nYear  Projected  Actual     Difference")
print("      R    D     R    D     R    D")
print("-"*40)

for year in [2016, 2018, 2020]:
    proj = detailed_projections[year]
    actual = results[year]
    
    diff_r = proj['R'] - actual['R']
    diff_d = proj['D'] - actual['D']
    
    print(f"{year}  {proj['R']:3d}  {proj['D']:3d}   "
          f"{actual['R']:3d}  {actual['D']:3d}   "
          f"{diff_r:+4d} {diff_d:+4d}")

# Final average
final_avg = np.mean([detailed_projections[y]['R'] - results[y]['R'] for y in [2016, 2018, 2020]])

print(f"\nFinal average R seat difference: {final_avg:+.1f}")

print("\n" + "="*80)
print("CONCLUSION")
print("="*80)

if final_avg > 0:
    print(f"\nBased on reverse engineering from actual 2022-2024 performance:")
    print(f"The current districts would have given Republicans {final_avg:.1f} MORE seats on average")
else:
    print(f"\nBased on reverse engineering from actual 2022-2024 performance:")
    print(f"The current districts would have given Republicans {-final_avg:.1f} FEWER seats on average")

print("\nKey findings:")
print(f"- Current districts have {very_safe_r + safe_r} Safe/Very Safe R districts")
print(f"- Current districts have {very_safe_d + safe_d} Safe/Very Safe D districts")
print(f"- Average R efficiency factor: {avg_r_efficiency:.2f}")
print(f"- Environment sensitivity: {seats_per_point:.1f} seats per point")

# Save results
output = {
    'methodology': 'reverse_engineering_from_current',
    'current_performance': current_performance,
    'projections': detailed_projections,
    'environments': environments,
    'average_difference': final_avg
}

with open('reverse_engineering_analysis.json', 'w') as f:
    json.dump(output, f, indent=2)

print("\n✓ Analysis saved to reverse_engineering_analysis.json")