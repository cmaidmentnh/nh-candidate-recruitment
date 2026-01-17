#!/usr/bin/env python3
"""
Calculate accurate PVI for NH House districts with normalization for unopposed races
Uses current (2022-2024) district boundaries and maps historical town votes to them
Normalizes districts where one party gets >75% to avoid skewing from unopposed races
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
    
    # Also check 2024 to ensure consistency
    df_2024 = pd.read_csv('2024_nh_all_results_comprehensive.csv')
    
    district_towns_2024 = defaultdict(set)
    for _, row in df_2024.iterrows():
        if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total', 'Unlabeled_Row_1', 'Unlabeled_Row_2', 'Unlabeled_Row_3']:
            county = row['county']
            district = str(row['district'])
            town = row['town']
            dist_key = f"{county}-{district}"
            district_towns_2024[dist_key].add(town)
    
    print(f"Found {len(district_towns)} districts with town assignments")
    
    return district_towns

def aggregate_town_votes_by_year(year):
    """Get total R and D votes by town for a given year"""
    print(f"Aggregating town votes for {year}...")
    
    town_votes = defaultdict(lambda: {'R': 0, 'D': 0, 'Other': 0, 'Total': 0})
    
    try:
        df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
        
        for _, row in df.iterrows():
            # Skip non-town rows
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
        
        print(f"  Found vote data for {len(town_votes)} towns")
        return town_votes
        
    except Exception as e:
        print(f"  Error processing {year}: {e}")
        return {}

def normalize_extreme_results(r_pct, d_pct, county_avg_r=None, county_avg_d=None):
    """
    Normalize results where one party gets >75% (likely unopposed)
    Use county average as a baseline for normalization
    """
    # If no county average provided, use state average of ~52% R, 48% D
    if county_avg_r is None:
        county_avg_r = 52.0
        county_avg_d = 48.0
    
    # If one party gets >75%, it's likely unopposed or nearly so
    if r_pct > 75:
        # Normalize to county average + 15 points (representing strong but not unopposed win)
        normalized_r = min(county_avg_r + 15, 65)
        normalized_d = 100 - normalized_r
        return normalized_r, normalized_d
    elif d_pct > 75:
        # Normalize to county average + 15 points
        normalized_d = min(county_avg_d + 15, 65)
        normalized_r = 100 - normalized_d
        return normalized_r, normalized_d
    else:
        # Keep original if not extreme
        return r_pct, d_pct

def calculate_district_pvi_normalized():
    """Calculate PVI using current district boundaries with historical data and normalization"""
    
    # Get current district boundaries
    district_towns = get_current_district_boundaries()
    
    # Get town-level votes for each year
    town_votes_by_year = {}
    for year in [2016, 2018, 2020, 2022, 2024]:
        town_votes_by_year[year] = aggregate_town_votes_by_year(year)
    
    # First pass: Calculate county averages for normalization
    print("\nCalculating county averages for normalization...")
    county_votes = defaultdict(lambda: {'R': 0, 'D': 0, 'Total': 0})
    
    for year, town_votes in town_votes_by_year.items():
        for town, votes in town_votes.items():
            # Find which county this town belongs to
            for dist_key, towns in district_towns.items():
                if town in towns:
                    county = dist_key.split('-')[0]
                    county_votes[county]['R'] += votes['R']
                    county_votes[county]['D'] += votes['D']
                    county_votes[county]['Total'] += votes['Total']
                    break
    
    county_averages = {}
    for county, votes in county_votes.items():
        total = votes['R'] + votes['D']
        if total > 0:
            county_averages[county] = {
                'R': (votes['R'] / total) * 100,
                'D': (votes['D'] / total) * 100
            }
    
    # Calculate district-level results using current boundaries
    print("\nCalculating district PVI scores with normalization...")
    district_results = []
    
    for dist_key, towns in district_towns.items():
        county, district = dist_key.split('-')
        county_avg = county_averages.get(county, {'R': 52.0, 'D': 48.0})
        
        # Aggregate votes across all years for this district's current towns
        yearly_results = {}
        normalized_yearly_results = {}
        total_r_votes = 0
        total_d_votes = 0
        total_other_votes = 0
        elections_with_data = 0
        
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
                r_pct = (year_r / year_total) * 100 if year_total > 0 else 0
                d_pct = (year_d / year_total) * 100 if year_total > 0 else 0
                
                # Store original results
                yearly_results[year] = {
                    'R': year_r,
                    'D': year_d,
                    'Other': year_other,
                    'Total': year_total,
                    'R_pct': r_pct,
                    'D_pct': d_pct,
                    'towns_found': towns_found
                }
                
                # Normalize if needed
                normalized_r_pct, normalized_d_pct = normalize_extreme_results(
                    r_pct, d_pct, county_avg['R'], county_avg['D']
                )
                
                # Calculate normalized vote counts
                r_d_total = year_r + year_d
                normalized_r = int(r_d_total * (normalized_r_pct / 100))
                normalized_d = int(r_d_total * (normalized_d_pct / 100))
                
                normalized_yearly_results[year] = {
                    'R': normalized_r,
                    'D': normalized_d,
                    'Other': year_other,
                    'Total': r_d_total + year_other,
                    'R_pct': normalized_r_pct,
                    'D_pct': normalized_d_pct,
                    'was_normalized': r_pct != normalized_r_pct
                }
                
                total_r_votes += normalized_r
                total_d_votes += normalized_d
                total_other_votes += year_other
                elections_with_data += 1
        
        # Calculate overall PVI using normalized data
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
            is_competitive = abs(pvi_raw) < 10 or avg_swing > 5
            
            # Get seat count from 2022 data
            seats = len([t for t in towns]) // 3  # Rough estimate, will refine
            
            # Determine actual seats from winners file
            try:
                winners_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
                actual_seats = len(winners_2022[(winners_2022['county'] == county) & 
                                               (winners_2022['district'] == int(district))])
                if actual_seats > 0:
                    seats = actual_seats
            except:
                pass
            
            # Check how many elections were normalized
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
                'avg_swing': round(avg_swing, 1),
                'is_competitive': bool(is_competitive),
                'yearly_results': yearly_results,
                'normalized_yearly_results': normalized_yearly_results
            })
    
    # Sort by county and district
    district_results.sort(key=lambda x: (x['county'], int(x['district'])))
    
    return district_results

def generate_normalized_pvi_report(district_results):
    """Generate comprehensive PVI report with normalized data"""
    
    # Write to CSV
    with open('nh_house_pvi_normalized.csv', 'w', newline='') as f:
        fieldnames = ['county', 'district', 'seats', 'town_count', 'towns', 'pvi_raw', 'pvi_label', 
                      'r_vote_pct', 'd_vote_pct', 'total_votes', 'avg_r_vote', 'avg_d_vote',
                      'elections_analyzed', 'elections_normalized', 'avg_swing', 'is_competitive']
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(district_results)
    
    print("\n" + "="*80)
    print("NEW HAMPSHIRE HOUSE DISTRICTS - NORMALIZED PVI ANALYSIS")
    print("Based on 2016-2024 Elections with Unopposed Race Normalization")
    print("="*80)
    
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
    
    # Count normalized elections
    total_normalized = sum(x['elections_normalized'] for x in district_results)
    districts_with_normalized = sum(1 for x in district_results if x['elections_normalized'] > 0)
    
    print(f"\nNormalization Statistics:")
    print(f"  Districts with normalized elections: {districts_with_normalized}")
    print(f"  Total normalized election results: {total_normalized}")
    
    # Most competitive districts
    print(f"\nMost Competitive Districts (smallest partisan lean):")
    competitive = sorted(district_results, key=lambda x: abs(x['pvi_raw']))[:15]
    for dist in competitive:
        normalized_flag = "*" if dist['elections_normalized'] > 0 else " "
        print(f"  {dist['county']}-{dist['district']}: {dist['pvi_label']:5s} " +
              f"(R: {dist['r_vote_pct']:4.1f}%, D: {dist['d_vote_pct']:4.1f}%) {normalized_flag}")
    
    print("\n* = District had unopposed races that were normalized")
    
    # Save detailed results
    with open('nh_house_pvi_normalized.json', 'w') as f:
        # Remove the yearly results for JSON serialization
        json_data = []
        for dist in district_results:
            dist_copy = dist.copy()
            dist_copy.pop('yearly_results', None)
            dist_copy.pop('normalized_yearly_results', None)
            json_data.append(dist_copy)
        json.dump(json_data, f, indent=2)
    
    print(f"\nResults saved to:")
    print(f"  - nh_house_pvi_normalized.csv (summary data)")
    print(f"  - nh_house_pvi_normalized.json (detailed data)")

if __name__ == "__main__":
    # Calculate normalized PVI
    district_results = calculate_district_pvi_normalized()
    
    # Generate report
    generate_normalized_pvi_report(district_results)