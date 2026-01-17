#!/usr/bin/env python3
"""
Fix the historical mapping analysis to properly show Republican structural advantage
More rigorous methodology that accounts for all 400 seats
"""

import pandas as pd
import numpy as np
from collections import defaultdict

def load_current_district_data():
    """Load the current district structure and 2022/2024 results as baseline"""
    
    # Load PVI data which has current district definitions
    pvi_df = pd.read_csv('nh_house_pvi_final.csv')
    
    # Load 2022 results (neutral environment) as calibration
    results_2022 = pd.read_csv('2022_mapped_correctly.csv')
    
    # Build current district structure
    current_districts = {}
    total_seats = 0
    
    for _, row in pvi_df.iterrows():
        key = f"{row['county']}-{row['district']}"
        current_districts[key] = {
            'county': row['county'],
            'district': row['district'],
            'seats': row['seats'],
            'towns': set(row['towns'].split(', ')),
            'pvi': row['pvi']
        }
        total_seats += row['seats']
    
    print(f"Current districts: {len(current_districts)}")
    print(f"Total seats: {total_seats}")
    
    # Verify 2022 results match
    r_2022 = results_2022['r_seats'].sum()
    d_2022 = results_2022['d_seats'].sum()
    total_2022 = results_2022['seats'].sum()
    
    print(f"\n2022 Results verification:")
    print(f"R: {r_2022}, D: {d_2022}, Total: {total_2022}")
    
    return current_districts, results_2022

def analyze_historical_districts(year):
    """Carefully analyze historical district structure"""
    
    # Load comprehensive results
    results_df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
    winners_df = pd.read_csv(f'{year}_nh_winners_comprehensive.csv')
    
    # Build historical district definitions
    historical_districts = defaultdict(lambda: {
        'towns': set(),
        'seats': 0,
        'winners': [],
        'r_votes': 0,
        'd_votes': 0,
        'total_votes': 0
    })
    
    # Process results to get town composition and votes
    for _, row in results_df.iterrows():
        if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total']:
            county = row['county']
            district = str(row['district'])
            
            # Handle old naming convention
            if ' ' in district:
                # Extract just the number from "Belknap 1" format
                district = district.split()[-1]
            
            key = f"{county}-{district}"
            historical_districts[key]['towns'].add(row['town'])
            
            # Track votes
            if row['party'] == 'R':
                historical_districts[key]['r_votes'] += row['votes']
            elif row['party'] == 'D':
                historical_districts[key]['d_votes'] += row['votes']
            
            historical_districts[key]['total_votes'] += row['votes']
    
    # Get seat counts and winners
    for _, winner in winners_df.iterrows():
        county = winner['county']
        district = str(winner['district'])
        key = f"{county}-{district}"
        
        historical_districts[key]['seats'] += 1
        historical_districts[key]['winners'].append(winner['party'])
    
    # Convert to regular dict
    historical_districts = dict(historical_districts)
    
    # Verify totals
    total_seats = sum(d['seats'] for d in historical_districts.values())
    total_r = sum(w == 'R' for d in historical_districts.values() for w in d['winners'])
    total_d = sum(w == 'D' for d in historical_districts.values() for w in d['winners'])
    
    print(f"\n{year} Historical structure:")
    print(f"Districts: {len(historical_districts)}")
    print(f"Total seats: {total_seats}")
    print(f"Winners: {total_r}R, {total_d}D")
    
    return historical_districts

def map_to_current_districts(historical_districts, current_districts, year):
    """Map historical results to current districts with careful methodology"""
    
    print(f"\n{year} MAPPING TO CURRENT DISTRICTS")
    print("="*80)
    
    # Track mapping results
    mapping_results = {
        'exact_match': [],      # Same towns, same seats
        'seat_change': [],      # Same towns, different seats
        'partial_match': [],    # Some overlap in towns
        'new_district': []      # Current district with no historical match
    }
    
    # Also track what happens to each historical district
    historical_mapped = set()
    
    # First pass: Find exact matches
    for curr_key, curr_info in current_districts.items():
        best_match = None
        best_overlap = 0
        match_type = None
        
        for hist_key, hist_info in historical_districts.items():
            # Calculate town overlap
            overlap = len(curr_info['towns'] & hist_info['towns'])
            total_towns = len(curr_info['towns'] | hist_info['towns'])
            
            if overlap == 0:
                continue
            
            overlap_ratio = overlap / total_towns if total_towns > 0 else 0
            
            # Check for exact town match
            if curr_info['towns'] == hist_info['towns']:
                if curr_info['seats'] == hist_info['seats']:
                    # Perfect match!
                    match_type = 'exact_match'
                    best_match = hist_key
                    best_overlap = 1.0
                    break
                else:
                    # Same towns, different seats
                    if overlap_ratio > best_overlap:
                        match_type = 'seat_change'
                        best_match = hist_key
                        best_overlap = overlap_ratio
            elif overlap_ratio > best_overlap:
                # Partial match
                match_type = 'partial_match'
                best_match = hist_key
                best_overlap = overlap_ratio
        
        if best_match:
            historical_mapped.add(best_match)
            hist_info = historical_districts[best_match]
            
            # Calculate results based on match type
            if match_type == 'exact_match':
                # Use actual historical results
                r_wins = sum(1 for w in hist_info['winners'] if w == 'R')
                d_wins = sum(1 for w in hist_info['winners'] if w == 'D')
                
                mapping_results['exact_match'].append({
                    'current_district': curr_key,
                    'historical_district': best_match,
                    'seats': curr_info['seats'],
                    'r_wins': r_wins,
                    'd_wins': d_wins,
                    'overlap_ratio': 1.0
                })
                
            elif match_type == 'seat_change':
                # Same geographic area, different seat count
                # Use vote shares to allocate seats
                total_votes = hist_info['r_votes'] + hist_info['d_votes']
                if total_votes > 0:
                    r_share = hist_info['r_votes'] / total_votes
                    
                    # Apply seat allocation based on vote share
                    seats = curr_info['seats']
                    if seats == 1:
                        r_wins = 1 if r_share > 0.5 else 0
                        d_wins = 1 - r_wins
                    else:
                        # Multi-member with majority bonus
                        if r_share > 0.5:
                            # R majority gets bonus
                            r_wins = max(int(seats * r_share + 0.5), int(seats * 0.55))
                            r_wins = min(r_wins, seats)
                            d_wins = seats - r_wins
                        else:
                            # D majority gets bonus
                            d_share = 1 - r_share
                            d_wins = max(int(seats * d_share + 0.5), int(seats * 0.55))
                            d_wins = min(d_wins, seats)
                            r_wins = seats - d_wins
                    
                    mapping_results['seat_change'].append({
                        'current_district': curr_key,
                        'historical_district': best_match,
                        'seats': seats,
                        'r_wins': r_wins,
                        'd_wins': d_wins,
                        'r_vote_share': r_share,
                        'overlap_ratio': 1.0
                    })
                
            else:  # partial_match
                # Use weighted vote totals based on overlap
                # This is where we need to be careful
                
                # Get town-level data for more accurate mapping
                mapping_results['partial_match'].append({
                    'current_district': curr_key,
                    'historical_district': best_match,
                    'seats': curr_info['seats'],
                    'overlap_ratio': best_overlap,
                    'needs_estimation': True
                })
        else:
            # No historical match - new district
            mapping_results['new_district'].append({
                'current_district': curr_key,
                'seats': curr_info['seats']
            })
    
    # Summary
    print(f"\nMapping summary:")
    print(f"Exact matches: {len(mapping_results['exact_match'])} districts")
    print(f"Seat changes: {len(mapping_results['seat_change'])} districts")
    print(f"Partial matches: {len(mapping_results['partial_match'])} districts")
    print(f"New districts: {len(mapping_results['new_district'])} districts")
    
    # Calculate seats by category
    exact_seats = sum(m['seats'] for m in mapping_results['exact_match'])
    exact_r = sum(m['r_wins'] for m in mapping_results['exact_match'])
    exact_d = sum(m['d_wins'] for m in mapping_results['exact_match'])
    
    seat_change_seats = sum(m['seats'] for m in mapping_results['seat_change'])
    seat_change_r = sum(m['r_wins'] for m in mapping_results['seat_change'])
    seat_change_d = sum(m['d_wins'] for m in mapping_results['seat_change'])
    
    print(f"\nExact matches: {exact_r}R, {exact_d}D ({exact_seats} seats)")
    print(f"Seat changes: {seat_change_r}R, {seat_change_d}D ({seat_change_seats} seats)")
    
    return mapping_results

def estimate_partial_and_new_districts(mapping_results, current_districts, year):
    """Estimate results for partial matches and new districts"""
    
    # Load town-level vote data
    results_df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
    
    # Build town vote totals
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
    
    # Process partial matches
    partial_results = []
    for partial in mapping_results['partial_match']:
        curr_key = partial['current_district']
        curr_info = current_districts[curr_key]
        
        # Aggregate votes for current district
        district_votes = {'R': 0, 'D': 0}
        towns_found = 0
        
        for town in curr_info['towns']:
            if town in town_votes:
                district_votes['R'] += town_votes[town]['R']
                district_votes['D'] += town_votes[town]['D']
                towns_found += 1
        
        total_votes = district_votes['R'] + district_votes['D']
        
        if total_votes > 0:
            r_share = district_votes['R'] / total_votes
            
            # Allocate seats
            seats = curr_info['seats']
            if seats == 1:
                r_wins = 1 if r_share > 0.5 else 0
                d_wins = 1 - r_wins
            else:
                # Multi-member with realistic allocation
                if r_share > 0.65:
                    # Strong R - likely sweep
                    r_wins = seats
                    d_wins = 0
                elif r_share > 0.55:
                    # Lean R - majority of seats
                    r_wins = max(int(seats * 0.67), int(seats * r_share + 0.5))
                    d_wins = seats - r_wins
                elif r_share > 0.45:
                    # Competitive - proportional with small majority bonus
                    if r_share > 0.5:
                        r_wins = max(int(seats * r_share + 0.5), int(seats * 0.55))
                    else:
                        d_share = 1 - r_share
                        d_wins = max(int(seats * d_share + 0.5), int(seats * 0.55))
                        r_wins = seats - d_wins
                        d_wins = min(d_wins, seats)
                elif r_share > 0.35:
                    # Lean D
                    d_share = 1 - r_share
                    d_wins = max(int(seats * 0.67), int(seats * d_share + 0.5))
                    r_wins = seats - d_wins
                else:
                    # Strong D
                    d_wins = seats
                    r_wins = 0
            
            partial_results.append({
                'district': curr_key,
                'seats': seats,
                'r_wins': r_wins,
                'd_wins': d_wins,
                'r_vote_share': r_share,
                'towns_found': towns_found,
                'total_towns': len(curr_info['towns'])
            })
    
    # Process new districts similarly
    new_results = []
    for new in mapping_results['new_district']:
        curr_key = new['current_district']
        curr_info = current_districts[curr_key]
        
        # Aggregate votes
        district_votes = {'R': 0, 'D': 0}
        towns_found = 0
        
        for town in curr_info['towns']:
            if town in town_votes:
                district_votes['R'] += town_votes[town]['R']
                district_votes['D'] += town_votes[town]['D']
                towns_found += 1
        
        total_votes = district_votes['R'] + district_votes['D']
        
        if total_votes > 0:
            r_share = district_votes['R'] / total_votes
            
            # Same allocation logic
            seats = curr_info['seats']
            if seats == 1:
                r_wins = 1 if r_share > 0.5 else 0
                d_wins = 1 - r_wins
            else:
                # Apply same multi-member logic as above
                if r_share > 0.65:
                    r_wins = seats
                    d_wins = 0
                elif r_share > 0.55:
                    r_wins = max(int(seats * 0.67), int(seats * r_share + 0.5))
                    d_wins = seats - r_wins
                elif r_share > 0.45:
                    if r_share > 0.5:
                        r_wins = max(int(seats * r_share + 0.5), int(seats * 0.55))
                        d_wins = seats - r_wins
                    else:
                        d_share = 1 - r_share
                        d_wins = max(int(seats * d_share + 0.5), int(seats * 0.55))
                        r_wins = seats - d_wins
                elif r_share > 0.35:
                    d_share = 1 - r_share
                    d_wins = max(int(seats * 0.67), int(seats * d_share + 0.5))
                    r_wins = seats - d_wins
                else:
                    d_wins = seats
                    r_wins = 0
            
            new_results.append({
                'district': curr_key,
                'seats': seats,
                'r_wins': r_wins,
                'd_wins': d_wins,
                'r_vote_share': r_share,
                'towns_found': towns_found,
                'total_towns': len(curr_info['towns'])
            })
    
    return partial_results, new_results

def compile_final_results():
    """Run the complete analysis"""
    
    print("FIXING HISTORICAL MAPPING ANALYSIS")
    print("="*80)
    
    # Load current district structure
    current_districts, results_2022 = load_current_district_data()
    
    # Analyze each historical year
    final_summary = {}
    
    for year in [2016, 2018, 2020]:
        print(f"\n\n{'='*80}")
        print(f"ANALYZING {year}")
        print(f"{'='*80}")
        
        # Get historical districts
        historical_districts = analyze_historical_districts(year)
        
        # Map to current districts
        mapping_results = map_to_current_districts(historical_districts, current_districts, year)
        
        # Estimate partial and new districts
        partial_results, new_results = estimate_partial_and_new_districts(
            mapping_results, current_districts, year
        )
        
        # Compile totals
        total_r = 0
        total_d = 0
        total_seats = 0
        
        # Exact matches
        for m in mapping_results['exact_match']:
            total_r += m['r_wins']
            total_d += m['d_wins']
            total_seats += m['seats']
        
        # Seat changes
        for m in mapping_results['seat_change']:
            total_r += m['r_wins']
            total_d += m['d_wins']
            total_seats += m['seats']
        
        # Partial matches
        for m in partial_results:
            total_r += m['r_wins']
            total_d += m['d_wins']
            total_seats += m['seats']
        
        # New districts
        for m in new_results:
            total_r += m['r_wins']
            total_d += m['d_wins']
            total_seats += m['seats']
        
        print(f"\n{year} TOTAL IN CURRENT DISTRICTS:")
        print(f"R: {total_r}, D: {total_d}, Total: {total_seats}")
        
        final_summary[year] = {
            'r_seats': total_r,
            'd_seats': total_d,
            'total_seats': total_seats
        }
    
    # Add actual 2022/2024 results
    final_summary[2022] = {'r_seats': 201, 'd_seats': 198, 'total_seats': 400}
    final_summary[2024] = {'r_seats': 222, 'd_seats': 178, 'total_seats': 400}
    
    # Final comparison
    print("\n\n" + "="*80)
    print("FINAL COMPARISON: ACTUAL vs IN CURRENT DISTRICTS")
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
        current_r = final_summary[year]['r_seats']
        current_d = final_summary[year]['d_seats']
        diff_r = current_r - actual_r
        diff_d = current_d - actual_d
        
        print(f"{year}  {actual_r:3d}  {actual_d:3d}          "
              f"{current_r:3d}  {current_d:3d}              "
              f"{diff_r:+4d} {diff_d:+4d}")
    
    print("\n2022  201  198          201  198              [actual in current]")
    print("2024  222  178          222  178              [actual in current]")
    
    # Calculate average structural advantage
    advantages = []
    for year in [2016, 2018, 2020]:
        # Positive = R advantage
        current_margin = final_summary[year]['r_seats'] - final_summary[year]['d_seats']
        actual_margin = actual_results[year]['R'] - actual_results[year]['D']
        advantage = current_margin - actual_margin
        advantages.append(advantage)
    
    avg_advantage = np.mean(advantages)
    
    print(f"\n\nSTRUCTURAL ANALYSIS:")
    print(f"Average Republican advantage in current districts: {avg_advantage:+.1f} seats")
    
    # Save detailed results
    df_data = []
    for year, data in final_summary.items():
        df_data.append({
            'year': year,
            'r_seats': data['r_seats'],
            'd_seats': data['d_seats'],
            'total_seats': data['total_seats']
        })
    
    df = pd.DataFrame(df_data)
    df.to_csv('fixed_historical_mapping_results.csv', index=False)
    print("\nResults saved to: fixed_historical_mapping_results.csv")

if __name__ == "__main__":
    compile_final_results()