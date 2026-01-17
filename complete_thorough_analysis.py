#!/usr/bin/env python3
"""
Complete and thorough redistricting analysis without preconceptions
Account for all factors in the data
"""

import pandas as pd
import json
import numpy as np
from collections import defaultdict

print("COMPLETE REDISTRICTING ANALYSIS")
print("="*80)

# Load all necessary data
print("\nLOADING ALL DATA SOURCES...")

# 1. Current district structure
with open('current_district_structure.json', 'r') as f:
    current_districts = json.load(f)
print(f"✓ Loaded {len(current_districts)} current districts")

# 2. Historical election data
historical_data = {}
for year in [2016, 2018, 2020]:
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    historical_data[year] = df
    print(f"✓ Loaded {year} data: {len(df)} rows, {df['town'].nunique()} unique towns")

# 3. Current election data for comparison
current_data = {}
for year in [2022, 2024]:
    df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
    current_data[year] = df
    print(f"✓ Loaded {year} data: {len(df)} rows")

# 4. Winner data to verify seat counts
winner_data = {}
for year in [2016, 2018, 2020]:
    with open(f'nh_election_data/{year}_winners.json', 'r') as f:
        winner_data[year] = json.load(f)
    total_seats = sum(len(d['winners']) for d in winner_data[year].values())
    print(f"✓ Loaded {year} winners: {total_seats} total seats")

# 5. Get current district seat counts from 2022 winners
df_2022_winners = pd.read_csv('2022_nh_winners_comprehensive.csv')
current_seats = {}
for _, row in df_2022_winners.iterrows():
    key = f"{row['county']}-{row['district']}"
    current_seats[key] = current_seats.get(key, 0) + 1
print(f"✓ Current district seats: {sum(current_seats.values())} total")

print("\n" + "="*80)
print("STEP 1: ANALYZE HISTORICAL DISTRICT STRUCTURE")
print("="*80)

# Understand how districts were structured historically
for year in [2016, 2018, 2020]:
    print(f"\n{year} Historical Districts:")
    
    # Count unique districts
    df = historical_data[year]
    unique_districts = df['district'].unique()
    print(f"  Total districts: {len(unique_districts)}")
    
    # Check for multi-town districts
    district_towns = defaultdict(set)
    for _, row in df.iterrows():
        district_towns[row['district']].add(row['town'])
    
    multi_town = sum(1 for towns in district_towns.values() if len(towns) > 1)
    single_town = sum(1 for towns in district_towns.values() if len(towns) == 1)
    print(f"  Multi-town districts: {multi_town}")
    print(f"  Single-town districts: {single_town}")
    
    # Check seat distribution
    seat_counts = defaultdict(int)
    for dist, data in winner_data[year].items():
        seats = len(data['winners'])
        seat_counts[seats] += 1
    
    print(f"  Seat distribution:")
    for seats in sorted(seat_counts.keys()):
        print(f"    {seats}-member districts: {seat_counts[seats]}")

print("\n" + "="*80)
print("STEP 2: BUILD TOWN-LEVEL VOTING PATTERNS")
print("="*80)

# For each town, calculate its historical voting pattern
town_voting_patterns = {}

for year in [2016, 2018, 2020]:
    df = historical_data[year]
    
    for town in df['town'].unique():
        town_data = df[df['town'] == town]
        
        # Calculate R and D strength
        r_votes = town_data[town_data['party'] == 'R']['votes'].sum()
        d_votes = town_data[town_data['party'] == 'D']['votes'].sum()
        
        # Also count candidates to understand competitiveness
        r_candidates = len(town_data[town_data['party'] == 'R'])
        d_candidates = len(town_data[town_data['party'] == 'D'])
        
        if town not in town_voting_patterns:
            town_voting_patterns[town] = {}
        
        town_voting_patterns[town][year] = {
            'r_votes': r_votes,
            'd_votes': d_votes,
            'r_candidates': r_candidates,
            'd_candidates': d_candidates,
            'total_votes': r_votes + d_votes,
            'r_share': r_votes / (r_votes + d_votes) if (r_votes + d_votes) > 0 else 0
        }

# Calculate average patterns
print("\nCalculating average town voting patterns...")
town_averages = {}
for town, years in town_voting_patterns.items():
    if len(years) >= 2:  # Need at least 2 years of data
        r_shares = [y['r_share'] for y in years.values()]
        avg_r_share = np.mean(r_shares)
        volatility = np.std(r_shares)
        
        town_averages[town] = {
            'avg_r_share': avg_r_share,
            'volatility': volatility,
            'elections': len(years)
        }

print(f"✓ Calculated patterns for {len(town_averages)} towns")

# Categorize towns
very_r = sum(1 for t in town_averages.values() if t['avg_r_share'] > 0.65)
lean_r = sum(1 for t in town_averages.values() if 0.55 < t['avg_r_share'] <= 0.65)
competitive = sum(1 for t in town_averages.values() if 0.45 <= t['avg_r_share'] <= 0.55)
lean_d = sum(1 for t in town_averages.values() if 0.35 <= t['avg_r_share'] < 0.45)
very_d = sum(1 for t in town_averages.values() if t['avg_r_share'] < 0.35)

print(f"\nTown partisan breakdown:")
print(f"  Very R (>65%): {very_r}")
print(f"  Lean R (55-65%): {lean_r}")
print(f"  Competitive (45-55%): {competitive}")
print(f"  Lean D (35-45%): {lean_d}")
print(f"  Very D (<35%): {very_d}")

print("\n" + "="*80)
print("STEP 3: ANALYZE CURRENT DISTRICTS COMPOSITION")
print("="*80)

# For each current district, analyze its composition
current_district_analysis = {}

for dist_key, towns in current_districts.items():
    seats = current_seats.get(dist_key, 1)
    
    # Aggregate town patterns
    district_r_lean = 0
    district_d_lean = 0
    towns_with_data = 0
    town_details = []
    
    for town in towns:
        # Try exact match
        if town in town_averages:
            town_data = town_averages[town]
            towns_with_data += 1
        elif ' Ward ' in town:
            # Try alternate format
            alt_town = town.replace(' Ward ', ' Wd ')
            if alt_town in town_averages:
                town_data = town_averages[alt_town]
                towns_with_data += 1
            else:
                continue
        else:
            continue
        
        # Weight by historical turnout
        avg_votes = 0
        count = 0
        for year_data in town_voting_patterns.get(town, {}).values():
            avg_votes += year_data['total_votes']
            count += 1
        if count > 0:
            avg_votes = avg_votes / count
        
        weight = avg_votes if avg_votes > 0 else 1
        district_r_lean += town_data['avg_r_share'] * weight
        district_d_lean += (1 - town_data['avg_r_share']) * weight
        
        town_details.append({
            'town': town,
            'r_share': town_data['avg_r_share'],
            'weight': weight
        })
    
    # Calculate district lean
    total_weight = district_r_lean + district_d_lean
    if total_weight > 0:
        district_r_share = district_r_lean / total_weight
    else:
        district_r_share = 0.5
    
    current_district_analysis[dist_key] = {
        'seats': seats,
        'towns': len(towns),
        'towns_with_data': towns_with_data,
        'estimated_r_share': district_r_share,
        'town_details': town_details
    }

# Categorize current districts
print("\nCurrent district partisan estimates (based on historical town data):")
safe_r = sum(1 for d in current_district_analysis.values() if d['estimated_r_share'] > 0.58)
lean_r = sum(1 for d in current_district_analysis.values() if 0.52 < d['estimated_r_share'] <= 0.58)
tossup = sum(1 for d in current_district_analysis.values() if 0.48 <= d['estimated_r_share'] <= 0.52)
lean_d = sum(1 for d in current_district_analysis.values() if 0.42 <= d['estimated_r_share'] < 0.48)
safe_d = sum(1 for d in current_district_analysis.values() if d['estimated_r_share'] < 0.42)

print(f"  Safe R (>58%): {safe_r}")
print(f"  Lean R (52-58%): {lean_r}")
print(f"  Tossup (48-52%): {tossup}")
print(f"  Lean D (42-48%): {lean_d}")
print(f"  Safe D (<42%): {safe_d}")

print("\n" + "="*80)
print("STEP 4: PREDICT HISTORICAL OUTCOMES IN CURRENT DISTRICTS")
print("="*80)

# For each historical year, predict outcomes in current districts
def allocate_seats(r_share, seats, year_effect=0):
    """Allocate seats based on vote share and year effect"""
    # Apply year effect
    adjusted_r_share = r_share + year_effect
    adjusted_r_share = max(0.01, min(0.99, adjusted_r_share))
    
    if seats == 1:
        return (1, 0) if adjusted_r_share > 0.5 else (0, 1)
    else:
        # Multi-member districts - model winner-take-all tendency
        # Based on NH's plurality-at-large system
        
        # Calculate probability of sweep based on margin
        margin = abs(adjusted_r_share - 0.5) * 2  # 0 to 1 scale
        
        if adjusted_r_share > 0.5:
            # R advantage
            if margin > 0.3:  # >65% R
                # Very likely R sweep
                r_seats = seats
                d_seats = 0
            elif margin > 0.16:  # >58% R
                # R gets most seats
                r_seats = max(int(seats * 0.75), seats - 1)
                d_seats = seats - r_seats
            elif margin > 0.04:  # >52% R
                # R gets majority
                r_seats = max(int(seats * 0.6), (seats + 1) // 2)
                d_seats = seats - r_seats
            else:  # 50-52% R
                # Close to proportional
                r_seats = int(seats * adjusted_r_share + 0.5)
                d_seats = seats - r_seats
        else:
            # D advantage
            margin = abs(margin)  # Make positive
            if margin > 0.3:  # >65% D
                # Very likely D sweep
                d_seats = seats
                r_seats = 0
            elif margin > 0.16:  # >58% D
                # D gets most seats
                d_seats = max(int(seats * 0.75), seats - 1)
                r_seats = seats - d_seats
            elif margin > 0.04:  # >52% D
                # D gets majority
                d_seats = max(int(seats * 0.6), (seats + 1) // 2)
                r_seats = seats - d_seats
            else:  # 48-50% R (50-52% D)
                # Close to proportional
                d_seats = int(seats * (1 - adjusted_r_share) + 0.5)
                r_seats = seats - d_seats
        
        return (r_seats, d_seats)

# Calculate year effects from statewide results
year_effects = {}
for year in [2016, 2018, 2020]:
    df = historical_data[year]
    total_r = df[df['party'] == 'R']['votes'].sum()
    total_d = df[df['party'] == 'D']['votes'].sum()
    statewide_r_share = total_r / (total_r + total_d)
    
    # Compare to average
    all_r = []
    all_d = []
    for y in [2016, 2018, 2020]:
        df_y = historical_data[y]
        all_r.append(df_y[df_y['party'] == 'R']['votes'].sum())
        all_d.append(df_y[df_y['party'] == 'D']['votes'].sum())
    
    avg_r_share = sum(all_r) / (sum(all_r) + sum(all_d))
    year_effects[year] = statewide_r_share - avg_r_share

print("\nYear effects (deviation from 3-year average):")
for year, effect in year_effects.items():
    print(f"  {year}: {effect:+.3f} ({effect*100:+.1f}% for R)")

# Predict outcomes
predictions = {}
for year in [2016, 2018, 2020]:
    year_effect = year_effects[year]
    
    total_r = 0
    total_d = 0
    unallocated = 0
    
    district_results = []
    
    for dist_key, analysis in current_district_analysis.items():
        seats = analysis['seats']
        r_share = analysis['estimated_r_share']
        
        if analysis['towns_with_data'] == 0:
            # No data for this district
            unallocated += seats
            r_seats, d_seats = 0, 0
        else:
            r_seats, d_seats = allocate_seats(r_share, seats, year_effect)
        
        total_r += r_seats
        total_d += d_seats
        
        district_results.append({
            'district': dist_key,
            'seats': seats,
            'base_r_share': r_share,
            'adjusted_r_share': r_share + year_effect,
            'r_seats': r_seats,
            'd_seats': d_seats
        })
    
    predictions[year] = {
        'R': total_r,
        'D': total_d,
        'unallocated': unallocated,
        'districts': district_results
    }
    
    # Compare to actual
    actual_r = sum(1 for d in winner_data[year].values() for w in d['winners'] if w['party'] == 'R')
    actual_d = sum(1 for d in winner_data[year].values() for w in d['winners'] if w['party'] == 'D')
    
    print(f"\n{year} Results:")
    print(f"  Predicted in current districts: {total_r}R, {total_d}D")
    if unallocated > 0:
        print(f"  Unallocated seats: {unallocated}")
    print(f"  Actual in historical districts: {actual_r}R, {actual_d}D")
    print(f"  Difference: {total_r - actual_r:+d}R, {total_d - actual_d:+d}D")

# Save detailed results
for year in [2016, 2018, 2020]:
    df = pd.DataFrame(predictions[year]['districts'])
    df.to_csv(f'{year}_current_districts_prediction_final.csv', index=False)

print("\n" + "="*80)
print("STEP 5: FINAL ANALYSIS")
print("="*80)

# Calculate average impact
r_diffs = []
for year in [2016, 2018, 2020]:
    pred_r = predictions[year]['R']
    actual_r = sum(1 for d in winner_data[year].values() for w in d['winners'] if w['party'] == 'R')
    r_diffs.append(pred_r - actual_r)

avg_diff = np.mean(r_diffs)

print(f"\nAverage R seat difference: {avg_diff:+.1f}")
print(f"\nInterpretation:")
if avg_diff > 0:
    print(f"The current districts would have given Republicans an average of {avg_diff:.1f} MORE seats")
else:
    print(f"The current districts would have given Republicans an average of {-avg_diff:.1f} FEWER seats")

# Additional insights
print("\n\nADDITIONAL INSIGHTS:")
print("-"*60)

# Check data coverage
total_towns_in_districts = sum(len(towns) for towns in current_districts.values())
towns_with_voting_data = sum(d['towns_with_data'] for d in current_district_analysis.values())
print(f"\nData coverage:")
print(f"  Total towns in current districts: {total_towns_in_districts}")
print(f"  Towns with historical voting data: {towns_with_voting_data}")
print(f"  Coverage: {towns_with_voting_data/total_towns_in_districts*100:.1f}%")

# Multi-member district analysis
multi_member = sum(1 for d in current_district_analysis.values() if d['seats'] > 1)
multi_seats = sum(d['seats'] for d in current_district_analysis.values() if d['seats'] > 1)
print(f"\nMulti-member districts:")
print(f"  Count: {multi_member} districts")
print(f"  Total seats: {multi_seats}")
print(f"  Average size: {multi_seats/multi_member:.1f} seats")

# Save comprehensive analysis
summary = {
    'avg_r_difference': avg_diff,
    'predictions': {year: {'R': p['R'], 'D': p['D']} for year, p in predictions.items()},
    'actuals': {year: {
        'R': sum(1 for d in winner_data[year].values() for w in d['winners'] if w['party'] == 'R'),
        'D': sum(1 for d in winner_data[year].values() for w in d['winners'] if w['party'] == 'D')
    } for year in [2016, 2018, 2020]},
    'year_effects': year_effects,
    'district_categories': {
        'safe_r': safe_r,
        'lean_r': lean_r,
        'tossup': tossup,
        'lean_d': lean_d,
        'safe_d': safe_d
    }
}

with open('redistricting_analysis_complete.json', 'w') as f:
    json.dump(summary, f, indent=2)

print("\n✓ Complete analysis saved to redistricting_analysis_complete.json")