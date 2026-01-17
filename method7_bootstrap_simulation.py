#!/usr/bin/env python3
"""
Method 7: Bootstrap simulation with town-level variation
Account for local candidate effects and turnout variation
"""

import pandas as pd
import json
import numpy as np

print("METHOD 7: BOOTSTRAP SIMULATION WITH LOCAL EFFECTS")
print("="*80)

# Load data
current_districts = json.load(open('current_district_structure.json'))

# Get seat counts from 2022
seats_df = pd.read_csv('2022_nh_winners_comprehensive.csv')
district_seats = {}
for county in seats_df['county'].unique():
    for district in seats_df[seats_df['county'] == county]['district'].unique():
        key = f"{county}-{district}"
        district_seats[key] = len(seats_df[(seats_df['county'] == county) & (seats_df['district'] == district)])

# Load historical data and calculate town-level patterns
town_patterns = {}

for year in [2016, 2018, 2020]:
    df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
    
    for town in df['town'].unique():
        town_data = df[df['town'] == town]
        r_votes = town_data[town_data['party'] == 'R']['votes'].sum()
        d_votes = town_data[town_data['party'] == 'D']['votes'].sum()
        
        if town not in town_patterns:
            town_patterns[town] = {'years': {}}
        
        if r_votes + d_votes > 0:
            town_patterns[town]['years'][year] = {
                'r_share': r_votes / (r_votes + d_votes),
                'turnout': r_votes + d_votes
            }

# Calculate town volatility and base rates
for town, data in town_patterns.items():
    if len(data['years']) >= 2:
        r_shares = [y['r_share'] for y in data['years'].values()]
        data['avg_r_share'] = np.mean(r_shares)
        data['volatility'] = np.std(r_shares)
        data['avg_turnout'] = np.mean([y['turnout'] for y in data['years'].values()])

print(f"Analyzed {len(town_patterns)} towns with historical data")

# Run bootstrap simulations
def simulate_election(year_effect=0, n_sims=1000):
    """Simulate election with local variation"""
    
    results = []
    
    for sim in range(n_sims):
        district_outcomes = {}
        
        for dist_key, towns in current_districts.items():
            seats = district_seats.get(dist_key, 1)
            
            # Simulate district vote
            dist_r = 0
            dist_d = 0
            
            for town in towns:
                # Find town data
                town_data = None
                if town in town_patterns and 'avg_r_share' in town_patterns[town]:
                    town_data = town_patterns[town]
                elif ' Ward ' in town:
                    alt = town.replace(' Ward ', ' Wd ')
                    if alt in town_patterns and 'avg_r_share' in town_patterns[alt]:
                        town_data = town_patterns[alt]
                
                if town_data:
                    # Base rate with year effect
                    base_r = town_data['avg_r_share'] + year_effect
                    
                    # Add random variation based on historical volatility
                    volatility = town_data.get('volatility', 0.02)
                    local_variation = np.random.normal(0, volatility)
                    
                    # Candidate quality effect (random, mean 0)
                    candidate_effect = np.random.normal(0, 0.02)
                    
                    # Final R share
                    r_share = max(0, min(1, base_r + local_variation + candidate_effect))
                    
                    # Simulate turnout variation
                    base_turnout = town_data.get('avg_turnout', 1000)
                    turnout = max(100, int(base_turnout * np.random.uniform(0.8, 1.2)))
                    
                    # Calculate votes
                    town_r = int(turnout * r_share)
                    town_d = turnout - town_r
                    
                    dist_r += town_r
                    dist_d += town_d
            
            # Allocate seats
            if dist_r + dist_d > 0:
                r_share = dist_r / (dist_r + dist_d)
                
                if seats == 1:
                    r_seats = 1 if r_share > 0.5 else 0
                    d_seats = 1 - r_seats
                else:
                    # Multi-member with uncertainty
                    # Add noise to threshold
                    threshold_noise = np.random.normal(0, 0.02)
                    
                    if r_share + threshold_noise > 0.58:
                        # Likely R majority/sweep
                        if r_share > 0.65 and np.random.random() < 0.8:
                            r_seats = seats
                            d_seats = 0
                        else:
                            r_seats = max(seats // 2 + 1, int(seats * 0.67))
                            d_seats = seats - r_seats
                    elif r_share + threshold_noise < 0.42:
                        # Likely D majority/sweep
                        if r_share < 0.35 and np.random.random() < 0.8:
                            d_seats = seats
                            r_seats = 0
                        else:
                            d_seats = max(seats // 2 + 1, int(seats * 0.67))
                            r_seats = seats - d_seats
                    else:
                        # Competitive - more variation
                        if seats == 2:
                            r_seats = 1
                            d_seats = 1
                        else:
                            # Proportional with randomness
                            expected_r = seats * r_share
                            r_seats = int(expected_r + np.random.normal(0, 0.5))
                            r_seats = max(0, min(seats, r_seats))
                            d_seats = seats - r_seats
                
                district_outcomes[dist_key] = {'r': r_seats, 'd': d_seats}
        
        # Total for this simulation
        total_r = sum(d['r'] for d in district_outcomes.values())
        total_d = sum(d['d'] for d in district_outcomes.values())
        results.append({'r': total_r, 'd': total_d})
    
    return results

# Run simulations for each year
print("\n\nSIMULATION RESULTS (1000 runs each)")
print("-"*60)

# Calculate year effects
statewide_avg = np.mean([t['avg_r_share'] for t in town_patterns.values() if 'avg_r_share' in t])
year_effects = {
    2016: 0.028,   # R+2.8%
    2018: -0.042,  # D+4.2%
    2020: 0.006    # R+0.6%
}

for year, effect in year_effects.items():
    print(f"\n{year} (effect: {effect:+.1%}):")
    
    results = simulate_election(effect, n_sims=1000)
    
    # Calculate statistics
    r_seats = [r['r'] for r in results]
    d_seats = [r['d'] for r in results]
    
    avg_r = np.mean(r_seats)
    std_r = np.std(r_seats)
    p90_r = np.percentile(r_seats, 90)
    p10_r = np.percentile(r_seats, 10)
    
    print(f"  R seats: {avg_r:.1f} ± {std_r:.1f}")
    print(f"  90% confidence interval: {p10_r:.0f} - {p90_r:.0f}")
    print(f"  Probability R > 200: {sum(1 for r in r_seats if r > 200) / len(r_seats):.1%}")
    
    # Compare to actual
    actual = {2016: 226, 2018: 167, 2020: 213}
    print(f"  Actual R seats: {actual[year]}")
    print(f"  Difference: {avg_r - actual[year]:+.1f}")

# Run neutral environment simulation
print("\n\nNEUTRAL ENVIRONMENT SIMULATION")
print("-"*60)

neutral_results = simulate_election(0, n_sims=5000)
r_seats = [r['r'] for r in neutral_results]

print(f"Expected R seats in neutral environment: {np.mean(r_seats):.1f}")
print(f"Standard deviation: {np.std(r_seats):.1f}")
print(f"95% confidence interval: {np.percentile(r_seats, 2.5):.0f} - {np.percentile(r_seats, 97.5):.0f}")

# Distribution
print("\nSeat distribution:")
for seats in range(180, 221, 10):
    prob = sum(1 for r in r_seats if seats <= r < seats + 10) / len(r_seats)
    print(f"  {seats}-{seats+9}: {prob:.1%}")