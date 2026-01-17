#!/usr/bin/env python3
"""
Calculate accurate PVI for NH House districts accounting for redistricting
Uses current (2022-2024) district boundaries and maps historical town votes to them
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

def calculate_district_pvi_accurate():
    """Calculate PVI using current district boundaries with historical data"""
    
    # Get current district boundaries
    district_towns = get_current_district_boundaries()
    
    # Get town-level votes for each year
    town_votes_by_year = {}
    for year in [2016, 2018, 2020, 2022, 2024]:
        town_votes_by_year[year] = aggregate_town_votes_by_year(year)
    
    # Calculate district-level results using current boundaries
    print("\nCalculating district PVI scores...")
    district_results = []
    
    for dist_key, towns in district_towns.items():
        county, district = dist_key.split('-')
        
        # Aggregate votes across all years for this district's current towns
        yearly_results = {}
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
                yearly_results[year] = {
                    'R': year_r,
                    'D': year_d,
                    'Other': year_other,
                    'Total': year_total,
                    'R_pct': (year_r / year_total) * 100 if year_total > 0 else 0,
                    'D_pct': (year_d / year_total) * 100 if year_total > 0 else 0,
                    'towns_found': towns_found
                }
                total_r_votes += year_r
                total_d_votes += year_d
                total_other_votes += year_other
                elections_with_data += 1
        
        # Calculate overall PVI
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
            years_sorted = sorted(yearly_results.keys())
            for i in range(1, len(years_sorted)):
                prev_year = years_sorted[i-1]
                curr_year = years_sorted[i]
                if prev_year in yearly_results and curr_year in yearly_results:
                    swing = yearly_results[curr_year]['R_pct'] - yearly_results[prev_year]['R_pct']
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
                'avg_swing': round(avg_swing, 1),
                'is_competitive': bool(is_competitive),
                'yearly_results': yearly_results
            })
    
    # Sort by county and district
    district_results.sort(key=lambda x: (x['county'], int(x['district'])))
    
    return district_results

def generate_accurate_pvi_report(district_results):
    """Generate comprehensive PVI report with accurate data"""
    
    # Write to CSV
    with open('nh_house_pvi_accurate.csv', 'w', newline='') as f:
        fieldnames = ['county', 'district', 'seats', 'town_count', 'towns', 'pvi_raw', 'pvi_label', 
                      'r_vote_pct', 'd_vote_pct', 'total_votes', 'avg_r_vote', 'avg_d_vote',
                      'elections_analyzed', 'avg_swing', 'is_competitive']
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(district_results)
    
    print("\n" + "="*80)
    print("NEW HAMPSHIRE HOUSE DISTRICTS - ACCURATE PVI ANALYSIS")
    print("Based on 2016-2024 Elections using Current District Boundaries")
    print("="*80)
    
    # Count districts by PVI category
    safe_r = sum(1 for x in district_results if x['pvi_raw'] >= 15)
    likely_r = sum(1 for x in district_results if 10 <= x['pvi_raw'] < 15)
    lean_r = sum(1 for x in district_results if 5 <= x['pvi_raw'] < 10)
    tilt_r = sum(1 for x in district_results if 0 < x['pvi_raw'] < 5)
    tilt_d = sum(1 for x in district_results if -5 < x['pvi_raw'] < 0)
    lean_d = sum(1 for x in district_results if -10 < x['pvi_raw'] <= -5)
    likely_d = sum(1 for x in district_results if -15 < x['pvi_raw'] <= -10)
    safe_d = sum(1 for x in district_results if x['pvi_raw'] <= -15)
    
    print(f"\nDistrict Classifications ({len(district_results)} districts analyzed):")
    print(f"  Safe Republican (R+15 or more):     {safe_r:3d} districts")
    print(f"  Likely Republican (R+10 to R+14):   {likely_r:3d} districts")
    print(f"  Lean Republican (R+5 to R+9):       {lean_r:3d} districts")
    print(f"  Tilt Republican (R+1 to R+4):       {tilt_r:3d} districts")
    print(f"  Tilt Democratic (D+1 to D+4):       {tilt_d:3d} districts")
    print(f"  Lean Democratic (D+5 to D+9):       {lean_d:3d} districts")
    print(f"  Likely Democratic (D+10 to D+14):   {likely_d:3d} districts")
    print(f"  Safe Democratic (D+15 or more):     {safe_d:3d} districts")
    
    # Count seats by category
    safe_r_seats = sum(x['seats'] for x in district_results if x['pvi_raw'] >= 15)
    likely_r_seats = sum(x['seats'] for x in district_results if 10 <= x['pvi_raw'] < 15)
    lean_r_seats = sum(x['seats'] for x in district_results if 5 <= x['pvi_raw'] < 10)
    tilt_r_seats = sum(x['seats'] for x in district_results if 0 < x['pvi_raw'] < 5)
    tilt_d_seats = sum(x['seats'] for x in district_results if -5 < x['pvi_raw'] < 0)
    lean_d_seats = sum(x['seats'] for x in district_results if -10 < x['pvi_raw'] <= -5)
    likely_d_seats = sum(x['seats'] for x in district_results if -15 < x['pvi_raw'] <= -10)
    safe_d_seats = sum(x['seats'] for x in district_results if x['pvi_raw'] <= -15)
    
    total_seats = sum(x['seats'] for x in district_results)
    
    print(f"\nSeat Classifications ({total_seats} seats analyzed):")
    print(f"  Safe Republican seats:      {safe_r_seats:3d}")
    print(f"  Likely Republican seats:    {likely_r_seats:3d}")
    print(f"  Lean Republican seats:      {lean_r_seats:3d}")
    print(f"  Tilt Republican seats:      {tilt_r_seats:3d}")
    print(f"  Tilt Democratic seats:      {tilt_d_seats:3d}")
    print(f"  Lean Democratic seats:      {lean_d_seats:3d}")
    print(f"  Likely Democratic seats:    {likely_d_seats:3d}")
    print(f"  Safe Democratic seats:      {safe_d_seats:3d}")
    
    # Most competitive districts
    print(f"\nMost Competitive Districts (smallest partisan lean):")
    competitive = sorted(district_results, key=lambda x: abs(x['pvi_raw']))[:15]
    for dist in competitive:
        yearly = dist['yearly_results']
        trend = ""
        if len(yearly) >= 2:
            years = sorted(yearly.keys())
            if yearly[years[-1]]['R_pct'] > yearly[years[0]]['R_pct']:
                trend = "→R"
            elif yearly[years[-1]]['R_pct'] < yearly[years[0]]['R_pct']:
                trend = "→D"
            else:
                trend = "→"
        
        print(f"  {dist['county']}-{dist['district']}: {dist['pvi_label']:5s} " +
              f"(R: {dist['r_vote_pct']:4.1f}%, D: {dist['d_vote_pct']:4.1f}%) {trend}")
    
    # Counties by average PVI
    print(f"\nCounty-Level Summary:")
    counties = defaultdict(list)
    for dist in district_results:
        counties[dist['county']].append(dist)
    
    county_summary = []
    for county, districts in counties.items():
        avg_pvi = np.mean([d['pvi_raw'] for d in districts])
        r_districts = sum(1 for d in districts if d['pvi_raw'] > 0)
        d_districts = sum(1 for d in districts if d['pvi_raw'] < 0)
        even_districts = sum(1 for d in districts if d['pvi_raw'] == 0)
        
        county_summary.append({
            'county': county,
            'avg_pvi': avg_pvi,
            'r_districts': r_districts,
            'd_districts': d_districts,
            'even_districts': even_districts,
            'total_districts': len(districts)
        })
    
    county_summary.sort(key=lambda x: x['avg_pvi'], reverse=True)
    
    for cs in county_summary:
        if cs['avg_pvi'] > 0:
            lean = f"R+{int(round(abs(cs['avg_pvi'])))}"
        elif cs['avg_pvi'] < 0:
            lean = f"D+{int(round(abs(cs['avg_pvi'])))}"
        else:
            lean = "EVEN"
        
        print(f"  {cs['county']:12s}: {lean:5s} " +
              f"(R: {cs['r_districts']}, D: {cs['d_districts']}, Even: {cs['even_districts']})")
    
    # Save detailed results
    with open('nh_house_pvi_accurate.json', 'w') as f:
        json.dump(district_results, f, indent=2)
    
    print(f"\nResults saved to:")
    print(f"  - nh_house_pvi_accurate.csv (summary data)")
    print(f"  - nh_house_pvi_accurate.json (detailed data with yearly breakdowns)")

if __name__ == "__main__":
    # Calculate accurate PVI
    district_results = calculate_district_pvi_accurate()
    
    # Generate report
    generate_accurate_pvi_report(district_results)