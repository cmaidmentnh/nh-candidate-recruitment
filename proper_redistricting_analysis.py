#!/usr/bin/env python3
"""
Proper redistricting analysis that accounts for split towns and multi-member districts
This should show the Republican advantage in current maps
"""

import pandas as pd
import json
import numpy as np

# Load current district structure
with open('current_district_structure.json', 'r') as f:
    current_districts = json.load(f)

# Load 2022/2024 comprehensive data to understand current performance
df_2022 = pd.read_csv('2022_nh_all_results_comprehensive.csv')
df_2024 = pd.read_csv('2024_nh_all_results_comprehensive.csv')

print("PROPER REDISTRICTING ANALYSIS")
print("="*80)

# First, establish baseline performance in current districts
print("\nCURRENT DISTRICT PERFORMANCE (2022-2024)")
print("-"*60)

current_performance = {}
for dist_key in current_districts:
    county, dist_num = dist_key.split('-')
    dist_num = int(dist_num)
    
    # Get 2022 results
    dist_2022 = df_2022[(df_2022['county'] == county) & (df_2022['district'] == dist_num)]
    r_2022 = dist_2022[dist_2022['party'] == 'R']['votes'].sum()
    d_2022 = dist_2022[dist_2022['party'] == 'D']['votes'].sum()
    
    # Get 2024 results
    dist_2024 = df_2024[(df_2024['county'] == county) & (df_2024['district'] == dist_num)]
    r_2024 = dist_2024[dist_2024['party'] == 'R']['votes'].sum()
    d_2024 = dist_2024[dist_2024['party'] == 'D']['votes'].sum()
    
    # Average performance
    if (r_2022 + d_2022 > 0) and (r_2024 + d_2024 > 0):
        r_avg = (r_2022 + r_2024) / 2
        d_avg = (d_2022 + d_2024) / 2
        r_share = r_avg / (r_avg + d_avg)
        
        current_performance[dist_key] = {
            'r_share_2022': r_2022 / (r_2022 + d_2022) if (r_2022 + d_2022) > 0 else 0,
            'r_share_2024': r_2024 / (r_2024 + d_2024) if (r_2024 + d_2024) > 0 else 0,
            'r_share_avg': r_share
        }

# Categorize districts
safe_r = sum(1 for d in current_performance.values() if d['r_share_avg'] > 0.58)
lean_r = sum(1 for d in current_performance.values() if 0.52 < d['r_share_avg'] <= 0.58)
tossup = sum(1 for d in current_performance.values() if 0.48 <= d['r_share_avg'] <= 0.52)
lean_d = sum(1 for d in current_performance.values() if 0.42 <= d['r_share_avg'] < 0.48)
safe_d = sum(1 for d in current_performance.values() if d['r_share_avg'] < 0.42)

print(f"Safe R (>58%): {safe_r}")
print(f"Lean R (52-58%): {lean_r}")
print(f"Tossup (48-52%): {tossup}")
print(f"Lean D (42-48%): {lean_d}")
print(f"Safe D (<42%): {safe_d}")

# Now, instead of trying to map historical votes (which is problematic due to splits),
# let's use a different approach: 
# 1. Calculate the baseline partisan lean of each current district from 2022-2024
# 2. Apply historical year effects to predict outcomes

# Calculate year effects from actual results
year_effects = {
    2016: {'R': 226/400, 'D': 174/400},  # R+13 year
    2018: {'R': 167/400, 'D': 233/400},  # D+16.5 year
    2020: {'R': 213/400, 'D': 187/400},  # R+6.5 year
    2022: {'R': 201/400, 'D': 198/400},  # Neutral (R+0.75)
    2024: {'R': 222/400, 'D': 178/400}   # R+11 year
}

# Baseline is average of 2022-2024
baseline_r = (year_effects[2022]['R'] + year_effects[2024]['R']) / 2  # 0.5288
baseline_effect = baseline_r - 0.5  # +0.0288 (R+2.9% baseline)

# Calculate relative year effects
relative_effects = {}
for year in [2016, 2018, 2020]:
    year_r = year_effects[year]['R']
    relative_effects[year] = (year_r - 0.5) - baseline_effect

print(f"\n\nYEAR EFFECTS (relative to 2022-2024 baseline)")
print("-"*60)
for year, effect in relative_effects.items():
    print(f"{year}: {effect:+.3f} ({effect*100:+.1f}% for R)")

# Now predict historical outcomes in current districts
print("\n\nPREDICTED OUTCOMES IN CURRENT DISTRICTS")
print("="*80)

def predict_seat(r_share_baseline, year_effect, seats):
    """Predict seat allocation given baseline and year effect"""
    # Adjust baseline by year effect
    adjusted_r_share = r_share_baseline + year_effect
    adjusted_r_share = max(0, min(1, adjusted_r_share))  # Bound between 0 and 1
    
    if seats == 1:
        return (1, 0) if adjusted_r_share > 0.5 else (0, 1)
    else:
        # Multi-member with winner bonus
        if adjusted_r_share > 0.65:
            return (seats, 0)
        elif adjusted_r_share > 0.58:
            r_seats = max(seats - 1, int(seats * 0.75))
            return (r_seats, seats - r_seats)
        elif adjusted_r_share > 0.52:
            r_seats = max(int(seats * 0.6), int(seats * adjusted_r_share + 0.5))
            return (r_seats, seats - r_seats)
        elif adjusted_r_share > 0.48:
            r_seats = int(seats * adjusted_r_share + 0.5)
            return (r_seats, seats - r_seats)
        elif adjusted_r_share > 0.42:
            d_seats = max(int(seats * 0.6), int(seats * (1-adjusted_r_share) + 0.5))
            return (seats - d_seats, d_seats)
        elif adjusted_r_share > 0.35:
            d_seats = max(seats - 1, int(seats * 0.75))
            return (seats - d_seats, d_seats)
        else:
            return (0, seats)

# Get seat counts
seats_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
district_seats = {}
for county in seats_2022['county'].unique():
    for district in seats_2022[seats_2022['county'] == county]['district'].unique():
        key = f"{county}-{district}"
        count = len(seats_2022[(seats_2022['county'] == county) & (seats_2022['district'] == district)])
        district_seats[key] = count

# Predict for each year
predictions = {}
for year in [2016, 2018, 2020]:
    year_effect = relative_effects[year]
    total_r = 0
    total_d = 0
    
    for dist_key in current_districts:
        seats = district_seats.get(dist_key, 1)
        
        if dist_key in current_performance:
            baseline = current_performance[dist_key]['r_share_avg']
        else:
            baseline = 0.5  # No data, assume neutral
        
        r_seats, d_seats = predict_seat(baseline, year_effect, seats)
        total_r += r_seats
        total_d += d_seats
    
    predictions[year] = {'R': total_r, 'D': total_d}
    
    # Compare to actual
    actual_r = int(year_effects[year]['R'] * 400)
    actual_d = int(year_effects[year]['D'] * 400)
    
    print(f"\n{year}:")
    print(f"  Predicted in current districts: {total_r}R, {total_d}D")
    print(f"  Actual in historical districts: {actual_r}R, {actual_d}D")
    print(f"  Difference: {total_r - actual_r:+d}R, {total_d - actual_d:+d}D")

# Calculate average advantage
advantages = []
for year in [2016, 2018, 2020]:
    pred_r = predictions[year]['R']
    actual_r = int(year_effects[year]['R'] * 400)
    advantages.append(pred_r - actual_r)

avg_advantage = np.mean(advantages)

print("\n\nCONCLUSION")
print("="*80)
print(f"Average R seat difference in current districts: {avg_advantage:+.1f}")

if avg_advantage > 0:
    print(f"\nThe current districts would have given Republicans an average ADVANTAGE of {avg_advantage:.1f} seats")
    print("This is consistent with the expected impact of the 2022 redistricting.")
else:
    print(f"\nThe current districts would have given Republicans an average DISADVANTAGE of {-avg_advantage:.1f} seats")
    print("\nNote: This analysis assumes uniform swing from the 2022-2024 baseline.")
    print("Actual results may vary due to:")
    print("- Candidate quality differences")
    print("- Local issues and campaign effects")
    print("- Differential turnout patterns")
    print("- Split-ticket voting behaviors")