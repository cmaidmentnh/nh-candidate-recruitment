#!/usr/bin/env python3
"""
Calculate accurate PVI for NH House districts with advanced normalization
- Calculate yearly baseline from contested races (1R vs 1D)
- For multi-member districts, compare equal numbers of candidates
- Apply weighted normalization for uncontested races based on district history and year tilt
"""

import pandas as pd
import numpy as np
import csv
import json
from collections import defaultdict

def get_current_district_boundaries():
    """Extract which towns belong to which districts based on 2022/2024 data"""
    print("Extracting current district boundaries...")
    
    district_towns = defaultdict(set)
    
    # Use 2022 data as the baseline for current districts
    df_2022 = pd.read_csv('2022_nh_all_results_comprehensive.csv')
    
    for _, row in df_2022.iterrows():
        if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total', 'Unlabeled_Row_1', 'Unlabeled_Row_2', 'Unlabeled_Row_3']:
            county = row['county']
            district = str(row['district'])
            town = row['town']
            dist_key = f"{county}-{district}"
            district_towns[dist_key].add(town)
    
    print(f"Found {len(district_towns)} districts with town assignments")
    
    return district_towns

def calculate_yearly_baseline(year):
    """
    Calculate statewide baseline for a year based on contested races
    Returns the overall R vs D tilt for that election year
    """
    print(f"\nCalculating baseline for {year}...")
    
    try:
        df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
        winners = pd.read_csv(f'{year}_nh_winners_comprehensive.csv')
    except:
        print(f"  Could not load data for {year}")
        return 0, {}
    
    # Group by county-district to analyze each race
    district_results = defaultdict(lambda: {'R_candidates': [], 'D_candidates': [], 'seats': 0})
    
    # Get seat counts from winners
    for _, winner in winners.iterrows():
        dist_key = f"{winner['county']}-{winner['district']}"
        district_results[dist_key]['seats'] += 1
    
    # Aggregate votes by candidate
    candidate_votes = defaultdict(lambda: {'votes': 0, 'party': '', 'county': '', 'district': ''})
    
    for _, row in df.iterrows():
        if row['town'] in ['District Total', 'Recount Total', 'Court Ordered Recount Total'] or 'Unlabeled' in str(row['town']):
            continue
        
        key = f"{row['county']}-{row['district']}-{row['candidate']}-{row['party']}"
        candidate_votes[key]['votes'] += row['votes']
        candidate_votes[key]['party'] = row['party']
        candidate_votes[key]['county'] = row['county']
        candidate_votes[key]['district'] = row['district']
    
    # Organize candidates by district
    for cand_key, cand_data in candidate_votes.items():
        if cand_data['votes'] > 0:
            dist_key = f"{cand_data['county']}-{cand_data['district']}"
            if cand_data['party'] == 'R':
                district_results[dist_key]['R_candidates'].append(cand_data['votes'])
            elif cand_data['party'] == 'D':
                district_results[dist_key]['D_candidates'].append(cand_data['votes'])
    
    # Calculate contested race results
    contested_r_votes = 0
    contested_d_votes = 0
    district_contested_info = {}
    
    for dist_key, data in district_results.items():
        # Sort candidates by votes (highest first)
        r_votes = sorted(data['R_candidates'], reverse=True)
        d_votes = sorted(data['D_candidates'], reverse=True)
        seats = data['seats']
        
        if seats == 0:
            continue
        
        # Determine how many candidates to compare
        r_count = len(r_votes)
        d_count = len(d_votes)
        
        # For contested comparison, use minimum of (seats, R candidates, D candidates)
        compare_count = min(seats, r_count, d_count)
        
        if compare_count > 0:
            # Sum top N candidates from each party
            r_sum = sum(r_votes[:compare_count])
            d_sum = sum(d_votes[:compare_count])
            
            contested_r_votes += r_sum
            contested_d_votes += d_sum
            
            # Store district-specific info
            district_contested_info[dist_key] = {
                'contested': True,
                'compare_count': compare_count,
                'r_votes': r_sum,
                'd_votes': d_sum,
                'r_pct': (r_sum / (r_sum + d_sum)) * 100 if (r_sum + d_sum) > 0 else 0
            }
        else:
            # Uncontested or no valid comparison
            district_contested_info[dist_key] = {
                'contested': False,
                'r_candidates': r_count,
                'd_candidates': d_count,
                'seats': seats
            }
    
    # Calculate year baseline
    total_contested = contested_r_votes + contested_d_votes
    if total_contested > 0:
        r_pct = (contested_r_votes / total_contested) * 100
        d_pct = (contested_d_votes / total_contested) * 100
        year_tilt = r_pct - d_pct
        
        print(f"  {year} baseline: R {r_pct:.1f}% vs D {d_pct:.1f}% = {'+' if year_tilt > 0 else ''}{year_tilt:.1f}")
        print(f"  Based on {sum(1 for d in district_contested_info.values() if d['contested'])} contested districts")
        
        return year_tilt, district_contested_info
    else:
        print(f"  No contested races found for {year}")
        return 0, district_contested_info

def calculate_district_pvi_advanced():
    """Calculate PVI with advanced normalization"""
    
    # Get current district boundaries
    district_towns = get_current_district_boundaries()
    
    # Calculate yearly baselines
    yearly_baselines = {}
    yearly_district_info = {}
    
    for year in [2016, 2018, 2020, 2022, 2024]:
        baseline, district_info = calculate_yearly_baseline(year)
        yearly_baselines[year] = baseline
        yearly_district_info[year] = district_info
    
    # Get town-level votes for each year
    print("\nAggregating town-level votes...")
    town_votes_by_year = {}
    for year in [2016, 2018, 2020, 2022, 2024]:
        town_votes_by_year[year] = aggregate_town_votes_by_year(year)
    
    # Calculate district-level results
    print("\nCalculating district PVI scores with advanced normalization...")
    district_results = []
    
    for dist_key, towns in district_towns.items():
        county, district = dist_key.split('-')
        
        # Track raw and normalized results
        yearly_results = {}
        normalized_yearly_results = {}
        
        # First pass: collect all raw results
        for year, town_votes in town_votes_by_year.items():
            year_r = 0
            year_d = 0
            year_other = 0
            year_total = 0
            towns_found = 0
            
            for town in towns:
                if town in town_votes:
                    year_r += town_votes[town]['R']
                    year_d += town_votes[town]['D']
                    year_other += town_votes[town]['Other']
                    year_total += town_votes[town]['Total']
                    towns_found += 1
            
            if year_total > 0:
                yearly_results[year] = {
                    'R': year_r,
                    'D': year_d,
                    'Other': year_other,
                    'Total': year_total,
                    'R_pct': (year_r / year_total) * 100 if year_total > 0 else 0,
                    'D_pct': (year_d / year_total) * 100 if year_total > 0 else 0,
                    'towns_found': towns_found
                }
        
        # Calculate district's average lean across contested years
        contested_years = []
        for year, results in yearly_results.items():
            dist_info = yearly_district_info[year].get(dist_key, {})
            if dist_info.get('contested', False):
                contested_years.append({
                    'year': year,
                    'r_pct': dist_info['r_pct'],
                    'year_baseline': yearly_baselines[year]
                })
        
        # Calculate district's inherent lean (adjusted for year effects)
        if contested_years:
            district_lean_sum = 0
            for cy in contested_years:
                # District's R% minus expected R% for that year
                expected_r = 50 + (cy['year_baseline'] / 2)
                district_lean_sum += cy['r_pct'] - expected_r
            
            district_inherent_lean = district_lean_sum / len(contested_years)
        else:
            # No contested years - use raw data average
            total_r = sum(yr['R'] for yr in yearly_results.values())
            total_d = sum(yr['D'] for yr in yearly_results.values())
            if total_r + total_d > 0:
                district_inherent_lean = ((total_r / (total_r + total_d)) * 100) - 50
            else:
                district_inherent_lean = 0
        
        # Second pass: normalize uncontested races
        total_r_votes = 0
        total_d_votes = 0
        total_other_votes = 0
        elections_with_data = 0
        
        for year, results in yearly_results.items():
            dist_info = yearly_district_info[year].get(dist_key, {})
            year_baseline = yearly_baselines[year]
            
            if dist_info.get('contested', False):
                # Use actual contested results
                normalized_yearly_results[year] = {
                    'R': results['R'],
                    'D': results['D'],
                    'Other': results['Other'],
                    'Total': results['Total'],
                    'R_pct': results['R_pct'],
                    'D_pct': results['D_pct'],
                    'was_normalized': False
                }
            else:
                # Normalize uncontested race
                # Expected result = district inherent lean + year effect
                expected_r_margin = district_inherent_lean + year_baseline
                expected_r_pct = 50 + (expected_r_margin / 2)
                expected_d_pct = 100 - expected_r_pct
                
                # Constrain to reasonable bounds
                expected_r_pct = max(20, min(80, expected_r_pct))
                expected_d_pct = 100 - expected_r_pct
                
                # Apply normalization
                r_d_total = results['R'] + results['D']
                normalized_r = int(r_d_total * (expected_r_pct / 100))
                normalized_d = int(r_d_total * (expected_d_pct / 100))
                
                normalized_yearly_results[year] = {
                    'R': normalized_r,
                    'D': normalized_d,
                    'Other': results['Other'],
                    'Total': r_d_total + results['Other'],
                    'R_pct': expected_r_pct,
                    'D_pct': expected_d_pct,
                    'was_normalized': True,
                    'normalization_reason': f"Uncontested - applied district lean {district_inherent_lean:+.1f} + year tilt {year_baseline:+.1f}"
                }
            
            # Add to totals
            total_r_votes += normalized_yearly_results[year]['R']
            total_d_votes += normalized_yearly_results[year]['D']
            total_other_votes += normalized_yearly_results[year]['Other']
            elections_with_data += 1
        
        # Calculate final PVI
        total_votes = total_r_votes + total_d_votes + total_other_votes
        
        if total_votes > 0 and elections_with_data > 0:
            r_pct = (total_r_votes / total_votes) * 100
            d_pct = (total_d_votes / total_votes) * 100
            
            # PVI is the partisan lean (R% - D%)
            pvi_raw = r_pct - d_pct
            
            # Format PVI
            if pvi_raw > 0:
                pvi_label = f"R+{int(round(abs(pvi_raw)))}"
            elif pvi_raw < 0:
                pvi_label = f"D+{int(round(abs(pvi_raw)))}"
            else:
                pvi_label = "EVEN"
            
            # Calculate vote swings
            vote_swings = []
            years_sorted = sorted(normalized_yearly_results.keys())
            for i in range(1, len(years_sorted)):
                prev_year = years_sorted[i-1]
                curr_year = years_sorted[i]
                if prev_year in normalized_yearly_results and curr_year in normalized_yearly_results:
                    swing = normalized_yearly_results[curr_year]['R_pct'] - normalized_yearly_results[prev_year]['R_pct']
                    vote_swings.append(abs(swing))
            
            avg_swing = np.mean(vote_swings) if vote_swings else 0
            
            # Determine competitiveness
            # Only consider competitive if close margin, regardless of swing
            # High swing in safe districts often just means varying turnout
            is_competitive = abs(pvi_raw) < 10
            
            # Get seat count
            seats = get_seat_count(county, district)
            
            # Count normalized elections
            normalized_count = sum(1 for yr in normalized_yearly_results.values() if yr.get('was_normalized', False))
            
            district_results.append({
                'county': county,
                'district': district,
                'seats': seats,
                'town_count': len(towns),
                'towns': ', '.join(sorted(towns)),
                'pvi_raw': round(pvi_raw, 1),
                'pvi_label': pvi_label,
                'r_vote_pct': round(r_pct, 1),
                'd_vote_pct': round(d_pct, 1),
                'total_votes': total_votes,
                'avg_r_vote': total_r_votes // elections_with_data if elections_with_data > 0 else 0,
                'avg_d_vote': total_d_votes // elections_with_data if elections_with_data > 0 else 0,
                'elections_analyzed': elections_with_data,
                'elections_normalized': normalized_count,
                'contested_elections': len(contested_years),
                'district_inherent_lean': round(district_inherent_lean, 1),
                'avg_swing': round(avg_swing, 1),
                'is_competitive': bool(is_competitive),
                'yearly_results': yearly_results,
                'normalized_yearly_results': normalized_yearly_results
            })
    
    # Sort by county and district
    district_results.sort(key=lambda x: (x['county'], int(x['district'])))
    
    return district_results, yearly_baselines

def aggregate_town_votes_by_year(year):
    """Get total R and D votes by town for a given year"""
    town_votes = defaultdict(lambda: {'R': 0, 'D': 0, 'Other': 0, 'Total': 0})
    
    try:
        df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
        
        for _, row in df.iterrows():
            if row['town'] in ['District Total', 'Recount Total', 'Court Ordered Recount Total'] or 'Unlabeled' in str(row['town']):
                continue
            
            town = row['town']
            party = row['party']
            votes = row['votes']
            
            if party == 'R':
                town_votes[town]['R'] += votes
            elif party == 'D':
                town_votes[town]['D'] += votes
            else:
                town_votes[town]['Other'] += votes
            
            town_votes[town]['Total'] += votes
        
        return town_votes
        
    except Exception as e:
        print(f"  Error processing {year}: {e}")
        return {}

def get_seat_count(county, district):
    """Get seat count for a district"""
    try:
        winners_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
        seats = len(winners_2022[(winners_2022['county'] == county) & 
                                 (winners_2022['district'] == int(district))])
        return seats if seats > 0 else 1
    except:
        return 1

def generate_advanced_pvi_report(district_results, yearly_baselines):
    """Generate comprehensive PVI report with advanced normalization"""
    
    # Write to CSV
    with open('nh_house_pvi_advanced.csv', 'w', newline='') as f:
        fieldnames = ['county', 'district', 'seats', 'town_count', 'towns', 'pvi_raw', 'pvi_label', 
                      'r_vote_pct', 'd_vote_pct', 'total_votes', 'avg_r_vote', 'avg_d_vote',
                      'elections_analyzed', 'elections_normalized', 'contested_elections',
                      'district_inherent_lean', 'avg_swing', 'is_competitive']
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(district_results)
    
    print("\n" + "="*80)
    print("NEW HAMPSHIRE HOUSE DISTRICTS - ADVANCED NORMALIZED PVI ANALYSIS")
    print("Based on 2016-2024 Elections with Contest-Based Normalization")
    print("="*80)
    
    print("\nYearly Baseline Tilts (from contested races):")
    for year in sorted(yearly_baselines.keys()):
        tilt = yearly_baselines[year]
        party = "R" if tilt > 0 else "D"
        print(f"  {year}: {party}+{abs(tilt):.1f}")
    
    # Count districts by PVI category
    safe_r = sum(1 for x in district_results if x['pvi_raw'] >= 15)
    likely_r = sum(1 for x in district_results if 10 <= x['pvi_raw'] < 15)
    lean_r = sum(1 for x in district_results if 5 <= x['pvi_raw'] < 10)
    tilt_r = sum(1 for x in district_results if 0 < x['pvi_raw'] < 5)
    tilt_d = sum(1 for x in district_results if -5 < x['pvi_raw'] <= 0)
    lean_d = sum(1 for x in district_results if -10 < x['pvi_raw'] <= -5)
    likely_d = sum(1 for x in district_results if -15 < x['pvi_raw'] <= -10)
    safe_d = sum(1 for x in district_results if x['pvi_raw'] <= -15)
    
    print(f"\nDistrict Classifications ({len(district_results)} districts analyzed):")
    print(f"  Safe Republican (R+15 or more):     {safe_r:3d} districts")
    print(f"  Likely Republican (R+10 to R+14):   {likely_r:3d} districts")
    print(f"  Lean Republican (R+5 to R+9):       {lean_r:3d} districts")
    print(f"  Tilt Republican (R+1 to R+4):       {tilt_r:3d} districts")
    print(f"  Tilt Democratic (D+0 to D+4):       {tilt_d:3d} districts")
    print(f"  Lean Democratic (D+5 to D+9):       {lean_d:3d} districts")
    print(f"  Likely Democratic (D+10 to D+14):   {likely_d:3d} districts")
    print(f"  Safe Democratic (D+15 or more):     {safe_d:3d} districts")
    
    # Normalization statistics
    total_normalized = sum(x['elections_normalized'] for x in district_results)
    districts_with_normalized = sum(1 for x in district_results if x['elections_normalized'] > 0)
    never_contested = sum(1 for x in district_results if x['contested_elections'] == 0)
    
    print(f"\nNormalization Statistics:")
    print(f"  Districts with normalized elections: {districts_with_normalized}")
    print(f"  Total normalized election results: {total_normalized}")
    print(f"  Districts never contested: {never_contested}")
    
    # Most competitive districts
    print(f"\nMost Competitive Districts (smallest partisan lean):")
    competitive = sorted(district_results, key=lambda x: abs(x['pvi_raw']))[:15]
    for dist in competitive:
        normalized_flag = "*" if dist['elections_normalized'] > 0 else " "
        print(f"  {dist['county']}-{dist['district']}: {dist['pvi_label']:5s} " +
              f"(R: {dist['r_vote_pct']:4.1f}%, D: {dist['d_vote_pct']:4.1f}%) " +
              f"[{dist['contested_elections']}/{dist['elections_analyzed']} contested] {normalized_flag}")
    
    print("\n* = District had uncontested races that were normalized")
    
    # Save detailed results
    with open('nh_house_pvi_advanced.json', 'w') as f:
        # Remove the yearly results for JSON serialization
        json_data = []
        for dist in district_results:
            dist_copy = dist.copy()
            dist_copy.pop('yearly_results', None)
            dist_copy.pop('normalized_yearly_results', None)
            json_data.append(dist_copy)
        json.dump(json_data, f, indent=2)
    
    print(f"\nResults saved to:")
    print(f"  - nh_house_pvi_advanced.csv (summary data)")
    print(f"  - nh_house_pvi_advanced.json (detailed data)")

if __name__ == "__main__":
    # Calculate advanced normalized PVI
    district_results, yearly_baselines = calculate_district_pvi_advanced()
    
    # Generate report
    generate_advanced_pvi_report(district_results, yearly_baselines)