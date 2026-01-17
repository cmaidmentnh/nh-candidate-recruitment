#!/usr/bin/env python3
"""
Alternative redistricting analysis using different methodology
Focus on actual candidate performance rather than vote totals
"""

import pandas as pd
import json
import numpy as np
from collections import defaultdict, Counter

print("ALTERNATIVE REDISTRICTING ANALYSIS")
print("="*80)
print("Using candidate-based performance metrics\n")

# Load data
current_districts = json.load(open('current_district_structure.json'))
historical_winners = {}
historical_results = {}

for year in [2016, 2018, 2020]:
    historical_winners[year] = json.load(open(f'nh_election_data/{year}_winners.json'))
    historical_results[year] = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')

# Load current results for calibration
current_results = {}
for year in [2022, 2024]:
    current_results[year] = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')

# Get current seat counts
seats_df = pd.read_csv('2022_nh_winners_comprehensive.csv')
current_seats = {}
for county in seats_df['county'].unique():
    for district in seats_df[seats_df['county'] == county]['district'].unique():
        key = f"{county}-{district}"
        current_seats[key] = len(seats_df[(seats_df['county'] == county) & (seats_df['district'] == district)])

print("APPROACH 1: CANDIDATE WIN RATES BY TOWN")
print("="*80)

# For each town, calculate the win rate for R and D candidates
town_win_rates = {}

for year in [2016, 2018, 2020]:
    print(f"\nAnalyzing {year} candidate performance...")
    
    # First, identify who won in each historical district
    district_winners = {}
    for dist, info in historical_winners[year].items():
        winners = [w['party'] for w in info['winners']]
        district_winners[dist] = Counter(winners)
    
    # Now, for each town, see how candidates from that town performed
    df = historical_results[year]
    
    for town in df['town'].unique():
        town_data = df[df['town'] == town]
        district = town_data['district'].iloc[0]
        
        # Get the winners in this district
        if district in district_winners:
            winners = district_winners[district]
            total_seats = sum(winners.values())
            
            # Calculate success rate for each party
            r_candidates = len(town_data[town_data['party'] == 'R'])
            d_candidates = len(town_data[town_data['party'] == 'D'])
            
            r_wins = winners.get('R', 0)
            d_wins = winners.get('D', 0)
            
            # Calculate win rates
            r_win_rate = r_wins / r_candidates if r_candidates > 0 else 0
            d_win_rate = d_wins / d_candidates if d_candidates > 0 else 0
            
            # Also get vote performance
            r_votes = town_data[town_data['party'] == 'R']['votes'].sum()
            d_votes = town_data[town_data['party'] == 'D']['votes'].sum()
            
            if town not in town_win_rates:
                town_win_rates[town] = {}
            
            town_win_rates[town][year] = {
                'r_win_rate': r_win_rate,
                'd_win_rate': d_win_rate,
                'r_candidates': r_candidates,
                'd_candidates': d_candidates,
                'r_wins': r_wins,
                'd_wins': d_wins,
                'total_seats': total_seats,
                'r_votes': r_votes,
                'd_votes': d_votes
            }

# Calculate average win rates
town_avg_performance = {}
for town, years in town_win_rates.items():
    if len(years) >= 2:
        avg_r_rate = np.mean([y['r_win_rate'] for y in years.values()])
        avg_d_rate = np.mean([y['d_win_rate'] for y in years.values()])
        
        # Calculate competitive advantage
        if avg_r_rate + avg_d_rate > 0:
            r_advantage = avg_r_rate / (avg_r_rate + avg_d_rate)
        else:
            # Use vote share as fallback
            total_r_votes = sum(y['r_votes'] for y in years.values())
            total_d_votes = sum(y['d_votes'] for y in years.values())
            if total_r_votes + total_d_votes > 0:
                r_advantage = total_r_votes / (total_r_votes + total_d_votes)
            else:
                r_advantage = 0.5
        
        town_avg_performance[town] = {
            'r_win_rate': avg_r_rate,
            'd_win_rate': avg_d_rate,
            'r_advantage': r_advantage,
            'elections': len(years)
        }

print(f"\nCalculated performance metrics for {len(town_avg_performance)} towns")

print("\n" + "="*80)
print("APPROACH 2: DISTRICT COMPETITIVENESS INDEX")
print("="*80)

# For each current district, calculate a competitiveness index
district_competitiveness = {}

for dist_key, towns in current_districts.items():
    seats = current_seats.get(dist_key, 1)
    
    # Aggregate town performance
    total_r_advantage = 0
    total_weight = 0
    towns_found = 0
    
    for town in towns:
        # Try to find town data
        town_data = None
        if town in town_avg_performance:
            town_data = town_avg_performance[town]
        elif ' Ward ' in town:
            alt_town = town.replace(' Ward ', ' Wd ')
            if alt_town in town_avg_performance:
                town_data = town_avg_performance[alt_town]
        
        if town_data:
            towns_found += 1
            # Weight by number of elections
            weight = town_data['elections']
            total_r_advantage += town_data['r_advantage'] * weight
            total_weight += weight
    
    if total_weight > 0:
        district_r_advantage = total_r_advantage / total_weight
    else:
        district_r_advantage = 0.5
    
    # Calculate competitive index (0 = very competitive, 1 = not competitive)
    competitiveness = 1 - (2 * abs(district_r_advantage - 0.5))
    
    district_competitiveness[dist_key] = {
        'seats': seats,
        'r_advantage': district_r_advantage,
        'competitiveness': competitiveness,
        'towns_found': towns_found,
        'total_towns': len(towns)
    }

# Categorize districts
very_safe_r = sum(1 for d in district_competitiveness.values() if d['r_advantage'] > 0.7)
safe_r = sum(1 for d in district_competitiveness.values() if 0.6 < d['r_advantage'] <= 0.7)
lean_r = sum(1 for d in district_competitiveness.values() if 0.55 < d['r_advantage'] <= 0.6)
tossup = sum(1 for d in district_competitiveness.values() if 0.45 <= d['r_advantage'] <= 0.55)
lean_d = sum(1 for d in district_competitiveness.values() if 0.4 <= d['r_advantage'] < 0.45)
safe_d = sum(1 for d in district_competitiveness.values() if 0.3 <= d['r_advantage'] < 0.4)
very_safe_d = sum(1 for d in district_competitiveness.values() if d['r_advantage'] < 0.3)

print("\nDistrict categorization based on candidate performance:")
print(f"  Very Safe R (>70%): {very_safe_r}")
print(f"  Safe R (60-70%): {safe_r}")
print(f"  Lean R (55-60%): {lean_r}")
print(f"  Tossup (45-55%): {tossup}")
print(f"  Lean D (40-45%): {lean_d}")
print(f"  Safe D (30-40%): {safe_d}")
print(f"  Very Safe D (<30%): {very_safe_d}")

print("\n" + "="*80)
print("APPROACH 3: SIMULATE ELECTIONS WITH UNCERTAINTY")
print("="*80)

# Run Monte Carlo simulations to account for uncertainty
def simulate_election(district_data, year_modifier=0, n_simulations=1000):
    """Simulate election outcomes with uncertainty"""
    
    results = {'R': 0, 'D': 0}
    
    for _ in range(n_simulations):
        r_wins = 0
        d_wins = 0
        
        for dist_key, data in district_data.items():
            seats = data['seats']
            base_r = data['r_advantage']
            
            # Add year effect and random variation
            # Uncertainty increases with competitiveness
            uncertainty = data['competitiveness'] * 0.1  # up to 10% swing in competitive districts
            random_factor = np.random.normal(0, uncertainty)
            
            r_prob = base_r + year_modifier + random_factor
            r_prob = max(0, min(1, r_prob))
            
            # Allocate seats
            if seats == 1:
                if r_prob > 0.5:
                    r_wins += 1
                else:
                    d_wins += 1
            else:
                # Multi-member - use probability model
                if r_prob > 0.65:
                    # High probability of sweep
                    if np.random.random() < (r_prob - 0.5) * 2:
                        r_wins += seats
                    else:
                        r_wins += seats - 1
                        d_wins += 1
                elif r_prob > 0.55:
                    # R favored
                    r_expected = seats * r_prob
                    r_wins += max(int(r_expected + 0.5), (seats + 1) // 2)
                    d_wins += seats - (r_wins - r_wins + max(int(r_expected + 0.5), (seats + 1) // 2))
                elif r_prob > 0.45:
                    # Competitive
                    r_expected = seats * r_prob
                    r_wins += int(r_expected + 0.5)
                    d_wins += seats - int(r_expected + 0.5)
                elif r_prob > 0.35:
                    # D favored
                    d_expected = seats * (1 - r_prob)
                    d_actual = max(int(d_expected + 0.5), (seats + 1) // 2)
                    d_wins += d_actual
                    r_wins += seats - d_actual
                else:
                    # High probability of D sweep
                    if np.random.random() < (0.5 - r_prob) * 2:
                        d_wins += seats
                    else:
                        d_wins += seats - 1
                        r_wins += 1
        
        results['R'] += r_wins / n_simulations
        results['D'] += d_wins / n_simulations
    
    return results

# Calculate year modifiers based on statewide swings
year_modifiers = {
    2016: 0.025,   # Slightly R year
    2018: -0.08,   # Strong D year
    2020: 0.01     # Neutral/slight R
}

print("\nRunning election simulations...")

for year in [2016, 2018, 2020]:
    print(f"\n{year} Simulations:")
    
    # Run simulation
    sim_results = simulate_election(district_competitiveness, year_modifiers[year])
    
    pred_r = int(sim_results['R'] + 0.5)
    pred_d = int(sim_results['D'] + 0.5)
    
    # Get actual results
    actual_r = sum(1 for d in historical_winners[year].values() for w in d['winners'] if w['party'] == 'R')
    actual_d = sum(1 for d in historical_winners[year].values() for w in d['winners'] if w['party'] == 'D')
    
    print(f"  Simulated in current districts: {pred_r}R, {pred_d}D")
    print(f"  Actual in historical districts: {actual_r}R, {actual_d}D")
    print(f"  Difference: {pred_r - actual_r:+d}R, {pred_d - actual_d:+d}D")

print("\n" + "="*80)
print("APPROACH 4: GEOGRAPHIC CLUSTERING ANALYSIS")
print("="*80)

# Analyze how towns are clustered in current vs historical districts
print("\nAnalyzing geographic efficiency...")

# For each year, calculate packing/cracking metrics
for year in [2016, 2018, 2020]:
    df = historical_results[year]
    
    # Historical district efficiency
    hist_efficiency = {'R': [], 'D': []}
    
    for dist, info in historical_winners[year].items():
        dist_data = df[df['district'] == dist]
        
        r_votes = dist_data[dist_data['party'] == 'R']['votes'].sum()
        d_votes = dist_data[dist_data['party'] == 'D']['votes'].sum()
        
        if r_votes + d_votes > 0:
            r_share = r_votes / (r_votes + d_votes)
            
            # Count actual wins
            r_wins = sum(1 for w in info['winners'] if w['party'] == 'R')
            d_wins = sum(1 for w in info['winners'] if w['party'] == 'D')
            total_seats = len(info['winners'])
            
            # Calculate efficiency (seats won / vote share)
            if r_share > 0:
                r_eff = (r_wins / total_seats) / r_share
                hist_efficiency['R'].append(r_eff)
            if (1 - r_share) > 0:
                d_eff = (d_wins / total_seats) / (1 - r_share)
                hist_efficiency['D'].append(d_eff)
    
    avg_r_eff_hist = np.mean(hist_efficiency['R']) if hist_efficiency['R'] else 1
    avg_d_eff_hist = np.mean(hist_efficiency['D']) if hist_efficiency['D'] else 1
    
    print(f"\n{year} Historical efficiency:")
    print(f"  R efficiency: {avg_r_eff_hist:.2f}")
    print(f"  D efficiency: {avg_d_eff_hist:.2f}")

print("\n" + "="*80)
print("FINAL ALTERNATIVE ANALYSIS")
print("="*80)

# Combine all approaches for final estimate
final_predictions = {}

for year in [2016, 2018, 2020]:
    # Use simulation results as primary method
    sim_results = simulate_election(district_competitiveness, year_modifiers[year], n_simulations=5000)
    
    pred_r = int(sim_results['R'] + 0.5)
    pred_d = int(sim_results['D'] + 0.5)
    
    final_predictions[year] = {'R': pred_r, 'D': pred_d}

# Calculate differences
print("\nSummary of predictions using candidate performance model:")
print("\nYear  Predicted  Actual     Difference")
print("      R    D     R    D     R    D")
print("-"*40)

total_r_diff = 0
for year in [2016, 2018, 2020]:
    pred = final_predictions[year]
    actual_r = sum(1 for d in historical_winners[year].values() for w in d['winners'] if w['party'] == 'R')
    actual_d = sum(1 for d in historical_winners[year].values() for w in d['winners'] if w['party'] == 'D')
    
    diff_r = pred['R'] - actual_r
    diff_d = pred['D'] - actual_d
    total_r_diff += diff_r
    
    print(f"{year}  {pred['R']:3d}  {pred['D']:3d}   {actual_r:3d}  {actual_d:3d}   {diff_r:+4d} {diff_d:+4d}")

avg_diff = total_r_diff / 3
print(f"\nAverage R difference: {avg_diff:+.1f} seats")

if avg_diff > 0:
    print(f"\nThe current districts would have given Republicans {avg_diff:.1f} MORE seats on average")
else:
    print(f"\nThe current districts would have given Republicans {-avg_diff:.1f} FEWER seats on average")

# Save detailed results
output = {
    'methodology': 'candidate_performance_based',
    'district_analysis': district_competitiveness,
    'predictions': final_predictions,
    'average_r_difference': avg_diff
}

with open('alternative_redistricting_analysis.json', 'w') as f:
    json.dump(output, f, indent=2)

print("\n✓ Analysis saved to alternative_redistricting_analysis.json")