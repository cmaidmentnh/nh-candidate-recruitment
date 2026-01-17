#!/usr/bin/env python3
"""
Calculate comprehensive PVI (Partisan Voting Index) for NH House districts
Based on actual results from 2016, 2018, 2020, 2022, and 2024 elections
"""

import pandas as pd
import numpy as np
import csv
import json
from collections import defaultdict

def calculate_district_pvi():
    """Calculate PVI for each district based on all available election data"""
    
    # Dictionary to store results by district
    district_results = defaultdict(lambda: {
        'elections': {},
        'total_r_votes': 0,
        'total_d_votes': 0,
        'total_other_votes': 0,
        'elections_counted': 0,
        'r_wins': 0,
        'd_wins': 0,
        'seats': 0
    })
    
    # Process each year's winners
    years = [2016, 2018, 2020, 2022, 2024]
    
    for year in years:
        try:
            # Try to read the winners file
            winners_file = f'{year}_nh_winners_comprehensive.csv'
            if year == 2022:
                # Use the correct filename for 2022
                df = pd.read_csv('2022_nh_winners_comprehensive.csv')
            elif year == 2024:
                df = pd.read_csv('2024_nh_winners_comprehensive.csv')
            else:
                # For earlier years, we'll need to parse them first
                print(f"Processing {year} data...")
                # We'll use the comprehensive parser for each year
                import subprocess
                
                # Create a parser for this year
                parser_content = open('comprehensive_town_parser.py', 'r').read()
                parser_content = parser_content.replace('2022', str(year))
                parser_content = parser_content.replace('nh_election_data/*2022*.xls*', f'nh_election_data/*{year}*.xls*')
                
                with open(f'parse_{year}_temp.py', 'w') as f:
                    f.write(parser_content)
                
                # Run the parser
                result = subprocess.run(['python3', f'parse_{year}_temp.py'], capture_output=True, text=True)
                
                # Clean up temp file
                subprocess.run(['rm', f'parse_{year}_temp.py'])
                
                # Read the results
                df = pd.read_csv(f'{year}_nh_winners_comprehensive.csv')
            
            # Process winners for this year
            for _, row in df.iterrows():
                county = row['county']
                district = str(row['district'])
                dist_key = f"{county}-{district}"
                
                # Track party votes
                if row['party'] == 'R':
                    district_results[dist_key]['elections'][year] = {
                        'r_votes': row.get('votes', 0),
                        'd_votes': 0,
                        'r_win': 1
                    }
                    district_results[dist_key]['r_wins'] += 1
                elif row['party'] == 'D':
                    district_results[dist_key]['elections'][year] = {
                        'r_votes': 0,
                        'd_votes': row.get('votes', 0),
                        'd_win': 1
                    }
                    district_results[dist_key]['d_wins'] += 1
                
                district_results[dist_key]['seats'] += 1
            
            print(f"Processed {year}: {len(df)} winners")
            
        except Exception as e:
            print(f"Could not process {year}: {e}")
    
    # Now read the comprehensive vote data for 2022 and 2024 to get actual vote totals
    for year in [2022, 2024]:
        try:
            if year == 2022:
                vote_df = pd.read_csv('2022_nh_all_results_comprehensive.csv')
            else:
                vote_df = pd.read_csv('2024_nh_all_results_comprehensive.csv')
            
            # Aggregate votes by district
            district_votes = defaultdict(lambda: {'R': 0, 'D': 0, 'Other': 0})
            
            for _, row in vote_df.iterrows():
                if row['town'] not in ['District Total', 'Recount Total', 'Court Ordered Recount Total']:
                    dist_key = f"{row['county']}-{row['district']}"
                    party = row['party']
                    votes = row['votes']
                    
                    if party == 'R':
                        district_votes[dist_key]['R'] += votes
                    elif party == 'D':
                        district_votes[dist_key]['D'] += votes
                    else:
                        district_votes[dist_key]['Other'] += votes
            
            # Update district results with vote totals
            for dist_key, votes in district_votes.items():
                if dist_key in district_results:
                    district_results[dist_key]['total_r_votes'] += votes['R']
                    district_results[dist_key]['total_d_votes'] += votes['D']
                    district_results[dist_key]['total_other_votes'] += votes['Other']
                    district_results[dist_key]['elections_counted'] += 1
                    
                    # Update the election record
                    if year in district_results[dist_key]['elections']:
                        district_results[dist_key]['elections'][year]['r_votes'] = votes['R']
                        district_results[dist_key]['elections'][year]['d_votes'] = votes['D']
            
        except Exception as e:
            print(f"Could not process vote data for {year}: {e}")
    
    # Calculate PVI for each district
    pvi_results = []
    
    for dist_key, data in district_results.items():
        county, district = dist_key.split('-')
        
        # Calculate average performance
        if data['elections_counted'] > 0:
            total_votes = data['total_r_votes'] + data['total_d_votes'] + data['total_other_votes']
            if total_votes > 0:
                r_pct = (data['total_r_votes'] / total_votes) * 100
                d_pct = (data['total_d_votes'] / total_votes) * 100
                
                # PVI is typically calculated as the difference from 50%
                # Positive = Republican lean, Negative = Democratic lean
                pvi = r_pct - d_pct
                
                # Traditional PVI format (R+5, D+3, etc.)
                if pvi > 0:
                    pvi_label = f"R+{int(round(pvi))}"
                elif pvi < 0:
                    pvi_label = f"D+{int(round(abs(pvi)))}"
                else:
                    pvi_label = "EVEN"
                
                # Calculate consistency (how often the same party wins)
                total_elections = data['r_wins'] + data['d_wins']
                if total_elections > 0:
                    consistency = max(data['r_wins'], data['d_wins']) / total_elections
                else:
                    consistency = 0
                
                # Determine if it's a swing district
                is_swing = (abs(pvi) < 5) or (consistency < 0.8)
                
                pvi_results.append({
                    'county': county,
                    'district': district,
                    'seats': data['seats'] // len([y for y in years if y in data['elections']]),  # Average seats
                    'pvi_score': round(pvi, 1),
                    'pvi_label': pvi_label,
                    'r_vote_pct': round(r_pct, 1),
                    'd_vote_pct': round(d_pct, 1),
                    'total_votes': total_votes,
                    'elections_analyzed': data['elections_counted'],
                    'r_wins': data['r_wins'],
                    'd_wins': data['d_wins'],
                    'consistency': round(consistency, 2),
                    'is_swing': is_swing,
                    'elections_detail': data['elections']
                })
    
    # Sort by county and district
    pvi_results.sort(key=lambda x: (x['county'], int(x['district'])))
    
    return pvi_results

def generate_pvi_report(pvi_results):
    """Generate comprehensive PVI analysis report"""
    
    # Write detailed results to CSV
    with open('nh_house_pvi_analysis.csv', 'w', newline='') as f:
        fieldnames = ['county', 'district', 'seats', 'pvi_score', 'pvi_label', 
                      'r_vote_pct', 'd_vote_pct', 'total_votes', 'elections_analyzed',
                      'r_wins', 'd_wins', 'consistency', 'is_swing']
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(pvi_results)
    
    # Generate summary statistics
    print("\n" + "="*80)
    print("NEW HAMPSHIRE HOUSE DISTRICTS - COMPREHENSIVE PVI ANALYSIS")
    print("Based on 2016-2024 Election Results")
    print("="*80)
    
    # Count districts by PVI category
    safe_r = sum(1 for x in pvi_results if x['pvi_score'] >= 10)
    likely_r = sum(1 for x in pvi_results if 5 <= x['pvi_score'] < 10)
    lean_r = sum(1 for x in pvi_results if 0 < x['pvi_score'] < 5)
    tossup = sum(1 for x in pvi_results if x['pvi_score'] == 0)
    lean_d = sum(1 for x in pvi_results if -5 < x['pvi_score'] < 0)
    likely_d = sum(1 for x in pvi_results if -10 < x['pvi_score'] <= -5)
    safe_d = sum(1 for x in pvi_results if x['pvi_score'] <= -10)
    
    print(f"\nDistrict Classifications (203 total):")
    print(f"  Safe Republican (R+10 or more):    {safe_r:3d} districts")
    print(f"  Likely Republican (R+5 to R+9):    {likely_r:3d} districts")
    print(f"  Lean Republican (R+1 to R+4):      {lean_r:3d} districts")
    print(f"  Tossup (EVEN):                     {tossup:3d} districts")
    print(f"  Lean Democratic (D+1 to D+4):      {lean_d:3d} districts")
    print(f"  Likely Democratic (D+5 to D+9):    {likely_d:3d} districts")
    print(f"  Safe Democratic (D+10 or more):    {safe_d:3d} districts")
    
    # Count seats by category
    safe_r_seats = sum(x['seats'] for x in pvi_results if x['pvi_score'] >= 10)
    likely_r_seats = sum(x['seats'] for x in pvi_results if 5 <= x['pvi_score'] < 10)
    lean_r_seats = sum(x['seats'] for x in pvi_results if 0 < x['pvi_score'] < 5)
    tossup_seats = sum(x['seats'] for x in pvi_results if x['pvi_score'] == 0)
    lean_d_seats = sum(x['seats'] for x in pvi_results if -5 < x['pvi_score'] < 0)
    likely_d_seats = sum(x['seats'] for x in pvi_results if -10 < x['pvi_score'] <= -5)
    safe_d_seats = sum(x['seats'] for x in pvi_results if x['pvi_score'] <= -10)
    
    print(f"\nSeat Classifications (400 total):")
    print(f"  Safe Republican seats:     {safe_r_seats:3d}")
    print(f"  Likely Republican seats:   {likely_r_seats:3d}")
    print(f"  Lean Republican seats:     {lean_r_seats:3d}")
    print(f"  Tossup seats:              {tossup_seats:3d}")
    print(f"  Lean Democratic seats:     {lean_d_seats:3d}")
    print(f"  Likely Democratic seats:   {likely_d_seats:3d}")
    print(f"  Safe Democratic seats:     {safe_d_seats:3d}")
    
    # Identify swing districts
    swing_districts = [x for x in pvi_results if x['is_swing']]
    print(f"\nSwing Districts ({len(swing_districts)} total):")
    for dist in swing_districts[:20]:  # Show first 20
        print(f"  {dist['county']}-{dist['district']}: {dist['pvi_label']} " +
              f"(R wins: {dist['r_wins']}, D wins: {dist['d_wins']})")
    
    if len(swing_districts) > 20:
        print(f"  ... and {len(swing_districts) - 20} more")
    
    # County-level analysis
    print("\nCounty-Level Summary:")
    counties = sorted(set(x['county'] for x in pvi_results))
    
    for county in counties:
        county_districts = [x for x in pvi_results if x['county'] == county]
        avg_pvi = np.mean([x['pvi_score'] for x in county_districts])
        r_districts = sum(1 for x in county_districts if x['pvi_score'] > 0)
        d_districts = sum(1 for x in county_districts if x['pvi_score'] < 0)
        
        if avg_pvi > 0:
            county_lean = f"R+{abs(int(round(avg_pvi)))}"
        elif avg_pvi < 0:
            county_lean = f"D+{abs(int(round(avg_pvi)))}"
        else:
            county_lean = "EVEN"
        
        print(f"  {county:12s}: {county_lean:5s} (R districts: {r_districts}, D districts: {d_districts})")
    
    # Most competitive districts
    print("\nMost Competitive Districts (closest to EVEN):")
    competitive = sorted(pvi_results, key=lambda x: abs(x['pvi_score']))[:10]
    for dist in competitive:
        print(f"  {dist['county']}-{dist['district']}: {dist['pvi_label']} " +
              f"(R: {dist['r_vote_pct']}%, D: {dist['d_vote_pct']}%)")
    
    # Safest districts
    print("\nSafest Republican Districts:")
    safe_r_list = sorted([x for x in pvi_results if x['pvi_score'] > 0], 
                        key=lambda x: x['pvi_score'], reverse=True)[:5]
    for dist in safe_r_list:
        print(f"  {dist['county']}-{dist['district']}: {dist['pvi_label']} " +
              f"(R: {dist['r_vote_pct']}%, D: {dist['d_vote_pct']}%)")
    
    print("\nSafest Democratic Districts:")
    safe_d_list = sorted([x for x in pvi_results if x['pvi_score'] < 0], 
                        key=lambda x: x['pvi_score'])[:5]
    for dist in safe_d_list:
        print(f"  {dist['county']}-{dist['district']}: {dist['pvi_label']} " +
              f"(R: {dist['r_vote_pct']}%, D: {dist['d_vote_pct']}%)")
    
    # Save detailed results to JSON
    with open('nh_house_pvi_analysis.json', 'w') as f:
        json.dump(pvi_results, f, indent=2)
    
    return pvi_results

if __name__ == "__main__":
    # Calculate PVI for all districts
    pvi_results = calculate_district_pvi()
    
    # Generate comprehensive report
    generate_pvi_report(pvi_results)
    
    print(f"\nResults saved to:")
    print(f"  - nh_house_pvi_analysis.csv (summary data)")
    print(f"  - nh_house_pvi_analysis.json (detailed data with election history)")