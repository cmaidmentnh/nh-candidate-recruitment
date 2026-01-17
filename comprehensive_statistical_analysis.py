#!/usr/bin/env python3
"""
Comprehensive statistical analysis of NH House elections in current districts
More rigorous methodology accounting for all factors
"""

import pandas as pd
import numpy as np
from collections import defaultdict
import json
from scipy import stats
import matplotlib.pyplot as plt
import seaborn as sns

def load_all_data():
    """Load all necessary data files"""
    print("Loading comprehensive data...")
    
    # Current district definitions
    pvi_df = pd.read_csv('nh_house_pvi_final.csv')
    
    # Historical winners
    winners = {}
    for year in [2016, 2018, 2020, 2022, 2024]:
        winners[year] = pd.read_csv(f'{year}_nh_winners_comprehensive.csv')
    
    # Historical results
    results = {}
    for year in [2016, 2018, 2020, 2022, 2024]:
        results[year] = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
    
    return pvi_df, winners, results

def analyze_district_changes():
    """Comprehensively analyze which districts changed"""
    
    pvi_df, winners, results = load_all_data()
    
    # Build historical district definitions
    historical_districts = {}
    
    for year in [2016, 2018, 2020]:
        hist_dists = defaultdict(lambda: {'towns': set(), 'seats': 0, 'winners': []})
        
        # Get district definitions from results
        df = results[year]
        for _, row in df.iterrows():
            if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total'] and 'Unlabeled' not in str(row['town']):
                county = row['county']
                # Handle old district naming
                dist_str = str(row['district'])
                if ' ' in dist_str:
                    district = dist_str.split()[-1]
                else:
                    district = dist_str
                
                key = f"{county}-{district}"
                hist_dists[key]['towns'].add(row['town'])
        
        # Get seat counts from winners
        win_df = winners[year]
        for _, winner in win_df.iterrows():
            key = f"{winner['county']}-{winner['district']}"
            hist_dists[key]['seats'] += 1
            hist_dists[key]['winners'].append(winner['party'])
        
        historical_districts[year] = dict(hist_dists)
    
    # Current districts
    current_districts = {}
    for _, row in pvi_df.iterrows():
        key = f"{row['county']}-{row['district']}"
        current_districts[key] = {
            'towns': set(row['towns'].split(', ')),
            'seats': row['seats']
        }
    
    # Find truly unchanged districts (same towns AND same seats)
    unchanged_districts = {}
    changed_districts = {}
    
    for year in [2016, 2018, 2020]:
        unchanged_districts[year] = {}
        changed_districts[year] = {}
        
        for curr_key, curr_info in current_districts.items():
            found_match = False
            
            # Look for exact match in historical data
            for hist_key, hist_info in historical_districts[year].items():
                if (curr_info['towns'] == hist_info['towns'] and 
                    curr_info['seats'] == hist_info['seats']):
                    # Perfect match!
                    unchanged_districts[year][curr_key] = {
                        'historical_key': hist_key,
                        'seats': curr_info['seats'],
                        'winners': hist_info['winners'],
                        'towns': curr_info['towns']
                    }
                    found_match = True
                    break
            
            if not found_match:
                changed_districts[year][curr_key] = curr_info
    
    # Summary
    print("\nDISTRICT CHANGE ANALYSIS")
    print("="*80)
    
    for year in [2016, 2018, 2020]:
        unch = unchanged_districts[year]
        ch = changed_districts[year]
        
        unch_seats = sum(d['seats'] for d in unch.values())
        ch_seats = sum(d['seats'] for d in ch.values())
        
        print(f"\n{year}:")
        print(f"  Unchanged: {len(unch)} districts, {unch_seats} seats")
        print(f"  Changed: {len(ch)} districts, {ch_seats} seats")
        
        # Count actual winners in unchanged districts
        r_wins = sum(1 for d in unch.values() for w in d['winners'] if w == 'R')
        d_wins = sum(1 for d in unch.values() for w in d['winners'] if w == 'D')
        print(f"  Unchanged results: {r_wins}R, {d_wins}D")
    
    return unchanged_districts, changed_districts, historical_districts, current_districts

def build_statistical_model():
    """Build a model for predicting seat allocation from vote shares"""
    
    print("\nBUILDING STATISTICAL MODEL")
    print("="*80)
    
    # Analyze relationship between vote share and seat share in multi-member districts
    # Using 2022 and 2024 data where we have complete information
    
    pvi_df = pd.read_csv('nh_house_pvi_final.csv')
    
    # Load detailed results
    results_2022 = pd.read_csv('2022_nh_all_results_comprehensive.csv')
    results_2024 = pd.read_csv('2024_nh_all_results_comprehensive.csv')
    winners_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
    winners_2024 = pd.read_csv('2024_nh_winners_comprehensive.csv')
    
    # Analyze multi-member districts
    multi_member_data = []
    
    for year, results_df, winners_df in [(2022, results_2022, winners_2022), 
                                          (2024, results_2024, winners_2024)]:
        
        # Get vote totals by district
        district_votes = defaultdict(lambda: {'R': 0, 'D': 0, 'Other': 0})
        
        for _, row in results_df.iterrows():
            if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total']:
                key = f"{row['county']}-{row['district']}"
                party = row['party']
                votes = row['votes']
                
                if party == 'R':
                    district_votes[key]['R'] += votes
                elif party == 'D':
                    district_votes[key]['D'] += votes
                else:
                    district_votes[key]['Other'] += votes
        
        # Get seat allocation by district
        district_seats = defaultdict(lambda: {'R': 0, 'D': 0, 'Other': 0, 'Total': 0})
        
        for _, winner in winners_df.iterrows():
            key = f"{winner['county']}-{winner['district']}"
            party = winner['party']
            
            if party in ['R', 'D']:
                district_seats[key][party] += 1
            else:
                district_seats[key]['Other'] += 1
            district_seats[key]['Total'] += 1
        
        # Combine data
        for dist_key in district_votes:
            if dist_key in district_seats and district_seats[dist_key]['Total'] > 1:
                votes = district_votes[dist_key]
                seats = district_seats[dist_key]
                
                total_votes = votes['R'] + votes['D'] + votes['Other']
                if total_votes > 0 and votes['R'] + votes['D'] > 0:
                    r_vote_share = votes['R'] / (votes['R'] + votes['D'])
                    r_seat_share = seats['R'] / seats['Total'] if seats['Total'] > 0 else 0
                    
                    multi_member_data.append({
                        'year': year,
                        'district': dist_key,
                        'total_seats': seats['Total'],
                        'r_vote_share': r_vote_share,
                        'r_seat_share': r_seat_share,
                        'r_seats': seats['R'],
                        'd_seats': seats['D']
                    })
    
    # Analyze the relationship
    if multi_member_data:
        df = pd.DataFrame(multi_member_data)
        
        # Group by seat count
        for seats in sorted(df['total_seats'].unique()):
            if seats > 1:
                subset = df[df['total_seats'] == seats]
                if len(subset) > 5:
                    print(f"\n{seats}-member districts:")
                    print(f"  Sample size: {len(subset)}")
                    
                    # Analyze seat bonus for majority party
                    subset['majority_bonus'] = subset['r_seat_share'] - subset['r_vote_share']
                    avg_bonus = subset['majority_bonus'].mean()
                    
                    print(f"  Average majority bonus: {avg_bonus:.3f}")
                    
                    # Fit a model
                    if len(subset) > 10:
                        try:
                            from sklearn.linear_model import LinearRegression
                            X = subset[['r_vote_share']].values
                            y = subset['r_seat_share'].values
                            
                            model = LinearRegression()
                            model.fit(X, y)
                            
                            print(f"  Model: seat_share = {model.intercept_:.3f} + {model.coef_[0]:.3f} * vote_share")
                        except ImportError:
                            # Use numpy polyfit instead
                            X = subset['r_vote_share'].values
                            y = subset['r_seat_share'].values
                            
                            z = np.polyfit(X, y, 1)
                            print(f"  Model: seat_share = {z[1]:.3f} + {z[0]:.3f} * vote_share")
    
    return multi_member_data

def estimate_changed_districts_statistically(unchanged_districts, changed_districts):
    """Use statistical methods to estimate results in changed districts"""
    
    print("\nSTATISTICAL ESTIMATION FOR CHANGED DISTRICTS")
    print("="*80)
    
    # Load town-level vote data
    town_votes_by_year = {}
    
    for year in [2016, 2018, 2020]:
        results_df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
        
        town_votes = defaultdict(lambda: {'R': 0, 'D': 0, 'Other': 0})
        
        for _, row in results_df.iterrows():
            if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total']:
                town = row['town']
                party = row['party']
                votes = row['votes']
                
                if party == 'R':
                    town_votes[town]['R'] += votes
                elif party == 'D':
                    town_votes[town]['D'] += votes
                else:
                    town_votes[town]['Other'] += votes
        
        town_votes_by_year[year] = dict(town_votes)
    
    # Estimate results for changed districts
    estimates_by_year = {}
    
    for year in [2016, 2018, 2020]:
        estimates = []
        
        for dist_key, dist_info in changed_districts[year].items():
            # Aggregate votes for this district
            district_votes = {'R': 0, 'D': 0, 'Other': 0}
            towns_found = 0
            
            for town in dist_info['towns']:
                if town in town_votes_by_year[year]:
                    district_votes['R'] += town_votes_by_year[year][town]['R']
                    district_votes['D'] += town_votes_by_year[year][town]['D']
                    district_votes['Other'] += town_votes_by_year[year][town]['Other']
                    towns_found += 1
            
            total_rd = district_votes['R'] + district_votes['D']
            
            if total_rd > 0:
                r_vote_share = district_votes['R'] / total_rd
                
                # Apply seat allocation model
                seats = dist_info['seats']
                
                if seats == 1:
                    # Single member - winner take all
                    r_seats = 1 if r_vote_share > 0.5 else 0
                    d_seats = 1 - r_seats
                else:
                    # Multi-member - use empirical relationship
                    # Based on our analysis, majority party typically gets a bonus
                    
                    # Base allocation
                    r_seats_base = seats * r_vote_share
                    
                    # Apply majority bonus (approximately 10% seat bonus for majority)
                    if r_vote_share > 0.5:
                        # R majority
                        bonus = min(0.1 * seats, seats - r_seats_base)
                        r_seats = int(r_seats_base + bonus + 0.5)
                        d_seats = seats - r_seats
                    else:
                        # D majority
                        d_vote_share = 1 - r_vote_share
                        d_seats_base = seats * d_vote_share
                        bonus = min(0.1 * seats, seats - d_seats_base)
                        d_seats = int(d_seats_base + bonus + 0.5)
                        r_seats = seats - d_seats
                    
                    # Handle extreme cases
                    if r_vote_share > 0.65:
                        r_seats = seats
                        d_seats = 0
                    elif r_vote_share < 0.35:
                        r_seats = 0
                        d_seats = seats
                    else:
                        # Ensure we have valid values
                        r_seats = max(0, min(seats, r_seats))
                        d_seats = seats - r_seats
                
                estimates.append({
                    'district': dist_key,
                    'seats': seats,
                    'r_vote_share': r_vote_share,
                    'r_seats': r_seats,
                    'd_seats': d_seats,
                    'towns_found': towns_found,
                    'total_towns': len(dist_info['towns'])
                })
        
        estimates_by_year[year] = estimates
    
    return estimates_by_year

def compile_final_results():
    """Compile all results into final estimates"""
    
    # Get district analysis
    unchanged, changed, historical, current = analyze_district_changes()
    
    # Build statistical model
    model_data = build_statistical_model()
    
    # Estimate changed districts
    estimates = estimate_changed_districts_statistically(unchanged, changed)
    
    print("\n\nFINAL RESULTS: HISTORICAL ELECTIONS IN CURRENT DISTRICTS")
    print("="*80)
    
    final_results = {}
    
    for year in [2016, 2018, 2020]:
        # Count unchanged district results
        unch_r = sum(1 for d in unchanged[year].values() for w in d['winners'] if w == 'R')
        unch_d = sum(1 for d in unchanged[year].values() for w in d['winners'] if w == 'D')
        unch_seats = sum(d['seats'] for d in unchanged[year].values())
        
        # Count estimated results
        est_r = sum(e['r_seats'] for e in estimates[year])
        est_d = sum(e['d_seats'] for e in estimates[year])
        est_seats = sum(e['seats'] for e in estimates[year])
        
        # Total
        total_r = unch_r + est_r
        total_d = unch_d + est_d
        total_seats = unch_seats + est_seats
        
        final_results[year] = {
            'unchanged_r': unch_r,
            'unchanged_d': unch_d,
            'unchanged_seats': unch_seats,
            'estimated_r': est_r,
            'estimated_d': est_d,
            'estimated_seats': est_seats,
            'total_r': total_r,
            'total_d': total_d,
            'total_seats': total_seats
        }
        
        print(f"\n{year}:")
        print(f"  Unchanged districts: {unch_r}R, {unch_d}D ({unch_seats} seats)")
        print(f"  Changed districts: {est_r}R, {est_d}D ({est_seats} seats)")
        print(f"  TOTAL: {total_r}R, {total_d}D ({total_seats} seats)")
    
    # Compare with actual results
    print("\n\nCOMPARISON WITH ACTUAL RESULTS")
    print("="*80)
    
    actual_results = {
        2016: {'R': 226, 'D': 174},
        2018: {'R': 167, 'D': 233},
        2020: {'R': 213, 'D': 187},
        2022: {'R': 201, 'D': 198},
        2024: {'R': 222, 'D': 178}
    }
    
    print("\nYear  Actual Results    In Current Districts    Difference")
    print("      R    D            R    D                  R    D")
    print("-"*60)
    
    for year in [2016, 2018, 2020]:
        actual_r = actual_results[year]['R']
        actual_d = actual_results[year]['D']
        current_r = final_results[year]['total_r']
        current_d = final_results[year]['total_d']
        diff_r = current_r - actual_r
        diff_d = current_d - actual_d
        
        print(f"{year}  {actual_r:3d}  {actual_d:3d}          "
              f"{current_r:3d}  {current_d:3d}              "
              f"{diff_r:+4d} {diff_d:+4d}")
    
    print("\n2022  201  198          201  198              [actual in current]")
    print("2024  222  178          222  178              [actual in current]")
    
    # Calculate structural advantage
    print("\n\nSTRUCTURAL ANALYSIS")
    print("="*80)
    
    # Average difference between actual and current districts
    advantages = []
    for year in [2016, 2018, 2020]:
        # Positive = R advantage in current districts
        advantage = (final_results[year]['total_r'] - actual_results[year]['R']) - \
                   (final_results[year]['total_d'] - actual_results[year]['D'])
        advantages.append(advantage)
    
    avg_advantage = np.mean(advantages)
    
    print(f"\nAverage Republican advantage in current districts: {avg_advantage:+.1f} seats")
    
    # Environmental sensitivity
    print("\nEnvironmental sensitivity (using 2022-2024 actual data):")
    r_2022 = 201
    r_2024 = 222
    env_change = 4.4  # 2024 was R+4.4 vs 2022 neutral
    seats_per_point = (r_2024 - r_2022) / env_change
    
    print(f"  Seats per environment point: {seats_per_point:.1f}")
    
    # Save detailed results
    detailed_results = []
    for year in [2016, 2018, 2020, 2022, 2024]:
        if year in final_results:
            fr = final_results[year]
            detailed_results.append({
                'year': year,
                'unchanged_r': fr['unchanged_r'],
                'unchanged_d': fr['unchanged_d'],
                'estimated_r': fr['estimated_r'],
                'estimated_d': fr['estimated_d'],
                'total_r': fr['total_r'],
                'total_d': fr['total_d'],
                'total_seats': fr['total_seats']
            })
        else:
            # Actual results for 2022/2024
            detailed_results.append({
                'year': year,
                'unchanged_r': 0,
                'unchanged_d': 0,
                'estimated_r': 0,
                'estimated_d': 0,
                'total_r': actual_results[year]['R'],
                'total_d': actual_results[year]['D'],
                'total_seats': 400
            })
    
    df = pd.DataFrame(detailed_results)
    df.to_csv('historical_elections_in_current_districts_final.csv', index=False)
    print("\nDetailed results saved to: historical_elections_in_current_districts_final.csv")

if __name__ == "__main__":
    compile_final_results()