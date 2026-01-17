#!/usr/bin/env python3
"""
Map historical election results to current district boundaries
Only estimate when districts actually changed
"""

import pandas as pd
import json
from collections import defaultdict

def load_current_districts():
    """Load current district definitions from 2022/2024"""
    # Load PVI data which has current districts
    pvi_df = pd.read_csv('nh_house_pvi_final.csv')
    
    current_districts = {}
    for _, row in pvi_df.iterrows():
        county = row['county']
        district = str(row['district'])
        towns = set(row['towns'].split(', '))
        dist_key = f"{county}-{district}"
        current_districts[dist_key] = {
            'towns': towns,
            'seats': row['seats'],
            'county': county,
            'district': district
        }
    
    return current_districts

def load_historical_districts(year):
    """Load historical district definitions and results"""
    # Load winner data
    winners_df = pd.read_csv(f'{year}_nh_winners_comprehensive.csv')
    
    # Load raw results to get town-level data
    results_df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
    
    # Build district definitions from results
    historical_districts = defaultdict(lambda: {'towns': set(), 'winners': [], 'seats': 0})
    
    # Get towns for each district
    for _, row in results_df.iterrows():
        if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total'] and 'Unlabeled' not in str(row['town']):
            county = row['county']
            # Extract just the number from district names like "Belknap 1"
            district_str = str(row['district'])
            if ' ' in district_str:
                # For old format like "Belknap 1", extract just the number
                district = district_str.split()[-1]
            else:
                district = district_str
            town = row['town']
            dist_key = f"{county}-{district}"
            historical_districts[dist_key]['towns'].add(town)
            historical_districts[dist_key]['county'] = county
            historical_districts[dist_key]['district'] = district
    
    # Get winners for each district
    for _, winner in winners_df.iterrows():
        dist_key = f"{winner['county']}-{str(winner['district'])}"
        historical_districts[dist_key]['winners'].append(winner['party'])
        historical_districts[dist_key]['seats'] += 1
    
    return dict(historical_districts)

def find_exact_matches(current_districts, historical_districts):
    """Find districts that have exactly the same towns"""
    exact_matches = {}
    
    # Create reverse lookup by town set
    historical_by_towns = {}
    for hist_key, hist_data in historical_districts.items():
        towns_tuple = tuple(sorted(hist_data['towns']))
        historical_by_towns[towns_tuple] = hist_key
    
    # Find matches
    for curr_key, curr_data in current_districts.items():
        towns_tuple = tuple(sorted(curr_data['towns']))
        if towns_tuple in historical_by_towns:
            hist_key = historical_by_towns[towns_tuple]
            hist_data = historical_districts[hist_key]
            
            exact_matches[curr_key] = {
                'current_district': curr_key,
                'historical_district': hist_key,
                'towns': curr_data['towns'],
                'current_seats': curr_data['seats'],
                'historical_seats': hist_data['seats'],
                'historical_winners': hist_data['winners'],
                'exact_match': True
            }
    
    return exact_matches

def estimate_changed_districts(current_districts, historical_districts, year):
    """Estimate results for districts that changed"""
    # Load raw vote data
    results_df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
    
    # Aggregate votes by town
    town_votes = defaultdict(lambda: {'R': 0, 'D': 0, 'Other': 0})
    
    for _, row in results_df.iterrows():
        if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total'] and 'Unlabeled' not in str(row['town']):
            town = row['town']
            party = row['party']
            votes = row['votes']
            
            if party == 'R':
                town_votes[town]['R'] += votes
            elif party == 'D':
                town_votes[town]['D'] += votes
            else:
                town_votes[town]['Other'] += votes
    
    # Estimate for changed districts
    estimates = {}
    
    for curr_key, curr_data in current_districts.items():
        # Skip if we have an exact match
        if curr_key in find_exact_matches(current_districts, historical_districts):
            continue
        
        # Aggregate votes for this district's towns
        district_votes = {'R': 0, 'D': 0, 'Other': 0}
        towns_found = []
        
        for town in curr_data['towns']:
            if town in town_votes:
                district_votes['R'] += town_votes[town]['R']
                district_votes['D'] += town_votes[town]['D']
                district_votes['Other'] += town_votes[town]['Other']
                towns_found.append(town)
        
        # Estimate winners based on vote totals
        total_votes = district_votes['R'] + district_votes['D'] + district_votes['Other']
        if total_votes > 0:
            r_pct = district_votes['R'] / total_votes
            d_pct = district_votes['D'] / total_votes
            
            # For multi-member districts, allocate seats proportionally
            seats = curr_data['seats']
            if seats == 1:
                # Single member - winner take all
                if r_pct > d_pct:
                    est_winners = ['R']
                else:
                    est_winners = ['D']
            else:
                # Multi-member - proportional (but account for winner bonus)
                r_seats = round(seats * r_pct)
                d_seats = seats - r_seats
                
                # Ensure we don't exceed seat count
                if r_seats > seats:
                    r_seats = seats
                    d_seats = 0
                elif d_seats > seats:
                    d_seats = seats
                    r_seats = 0
                
                est_winners = ['R'] * r_seats + ['D'] * d_seats
            
            estimates[curr_key] = {
                'current_district': curr_key,
                'towns': curr_data['towns'],
                'towns_found': towns_found,
                'current_seats': seats,
                'estimated_winners': est_winners,
                'vote_totals': district_votes,
                'r_pct': r_pct,
                'd_pct': d_pct,
                'exact_match': False
            }
    
    return estimates

def compile_results_for_year(year):
    """Compile all results for a given year"""
    print(f"\n{'='*80}")
    print(f"Mapping {year} results to current districts")
    print('='*80)
    
    current_districts = load_current_districts()
    historical_districts = load_historical_districts(year)
    
    # Find exact matches
    exact_matches = find_exact_matches(current_districts, historical_districts)
    print(f"\nFound {len(exact_matches)} exact district matches")
    
    # Estimate changed districts
    estimates = estimate_changed_districts(current_districts, historical_districts, year)
    print(f"Estimated {len(estimates)} changed districts")
    
    # Combine results
    all_results = {}
    all_results.update(exact_matches)
    all_results.update(estimates)
    
    # Calculate totals
    r_seats_exact = sum(1 for match in exact_matches.values() for w in match['historical_winners'] if w == 'R')
    d_seats_exact = sum(1 for match in exact_matches.values() for w in match['historical_winners'] if w == 'D')
    
    r_seats_est = sum(1 for est in estimates.values() for w in est['estimated_winners'] if w == 'R')
    d_seats_est = sum(1 for est in estimates.values() for w in est['estimated_winners'] if w == 'D')
    
    total_r = r_seats_exact + r_seats_est
    total_d = d_seats_exact + d_seats_est
    
    print(f"\n{year} Results in Current Districts:")
    print(f"  Exact matches: {r_seats_exact}R, {d_seats_exact}D")
    print(f"  Estimates: {r_seats_est}R, {d_seats_est}D")
    print(f"  TOTAL: {total_r}R, {total_d}D ({total_r + total_d} seats)")
    
    # Show some examples
    print(f"\nExample exact matches:")
    for i, (dist_key, match) in enumerate(list(exact_matches.items())[:3]):
        county, dist = dist_key.split('-')
        hist_county, hist_dist = match['historical_district'].split('-')
        winners = match['historical_winners']
        r_count = winners.count('R')
        d_count = winners.count('D')
        print(f"  {county}-{dist} = {hist_county}-{hist_dist}: {r_count}R, {d_count}D ({match['historical_seats']} seats)")
    
    # Save detailed results
    output_data = []
    for dist_key, data in sorted(all_results.items()):
        county, district = dist_key.split('-')
        
        if data['exact_match']:
            winners = data['historical_winners']
            r_wins = winners.count('R')
            d_wins = winners.count('D')
            method = 'exact'
        else:
            winners = data['estimated_winners']
            r_wins = winners.count('R')
            d_wins = winners.count('D')
            method = 'estimated'
        
        output_data.append({
            'county': county,
            'district': district,
            'seats': data['current_seats'],
            'r_wins': r_wins,
            'd_wins': d_wins,
            'method': method,
            'towns': ', '.join(sorted(data['towns']))
        })
    
    output_df = pd.DataFrame(output_data)
    output_file = f'{year}_mapped_to_current_districts.csv'
    output_df.to_csv(output_file, index=False)
    print(f"\nDetailed results saved to {output_file}")
    
    return total_r, total_d, exact_matches, estimates

def main():
    """Map all historical years to current districts"""
    
    results_summary = {}
    
    for year in [2016, 2018, 2020]:
        r_seats, d_seats, exact_matches, estimates = compile_results_for_year(year)
        results_summary[year] = {
            'R': r_seats,
            'D': d_seats,
            'total': r_seats + d_seats,
            'exact_matches': len(exact_matches),
            'estimates': len(estimates)
        }
    
    # Compare with actual 2022/2024 results
    print(f"\n{'='*80}")
    print("Summary: Historical Results in Current Districts")
    print('='*80)
    print(f"{'Year':<6} {'R Seats':<10} {'D Seats':<10} {'Total':<8} {'Method':<30}")
    print('-'*60)
    
    for year in [2016, 2018, 2020]:
        r = results_summary[year]['R']
        d = results_summary[year]['D']
        total = results_summary[year]['total']
        exact = results_summary[year]['exact_matches']
        est = results_summary[year]['estimates']
        print(f"{year:<6} {r:<10} {d:<10} {total:<8} ({exact} exact, {est} estimated)")
    
    print('-'*60)
    print(f"{'2022':<6} {'201':<10} {'198':<10} {'399*':<8} (actual results)")
    print(f"{'2024':<6} {'222':<10} {'178':<10} {'400':<8} (actual results)")
    print("*2022 had 1 vacancy due to tie")

if __name__ == "__main__":
    main()