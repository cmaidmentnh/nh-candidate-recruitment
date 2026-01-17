#!/usr/bin/env python3
"""
Final PVI Analysis for NH House Districts
- Uses district inherent lean as the primary PVI metric
- Sophisticated competitiveness analysis including:
  - Margin + volatility
  - Crossover victories
  - Electoral environment sensitivity
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
    """Calculate statewide baseline for a year based on contested races"""
    print(f"\nCalculating baseline for {year}...")
    
    try:
        df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
        winners = pd.read_csv(f'{year}_nh_winners_comprehensive.csv')
    except:
        print(f"  Could not load data for {year}")
        return 0, {}
    
    # Group by county-district to analyze each race
    district_results = defaultdict(lambda: {'R_candidates': [], 'D_candidates': [], 'seats': 0})
    
    # Get seat counts and winner info
    district_winners = defaultdict(list)
    for _, winner in winners.iterrows():
        dist_key = f"{winner['county']}-{winner['district']}"
        district_results[dist_key]['seats'] += 1
        if winner['party'] in ['R', 'D']:
            district_winners[dist_key].append(winner['party'])
    
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
        winners = district_winners.get(dist_key, [])
        
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
                'r_pct': (r_sum / (r_sum + d_sum)) * 100 if (r_sum + d_sum) > 0 else 0,
                'winners': winners
            }
        else:
            # Uncontested or no valid comparison
            district_contested_info[dist_key] = {
                'contested': False,
                'r_candidates': r_count,
                'd_candidates': d_count,
                'seats': seats,
                'winners': winners
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

def calculate_district_pvi_final():
    """Calculate final PVI with sophisticated analysis"""
    
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
    print("\nCalculating final district PVI scores...")
    district_results = []
    
    for dist_key, towns in district_towns.items():
        county, district = dist_key.split('-')
        
        # Track all election results
        yearly_results = {}
        yearly_margins = []
        crossover_elections = []
        
        # First pass: collect all raw results and check for crossovers
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
                r_pct = (year_r / year_total) * 100
                d_pct = (year_d / year_total) * 100
                margin = r_pct - d_pct
                
                yearly_results[year] = {
                    'R': year_r,
                    'D': year_d,
                    'Other': year_other,
                    'Total': year_total,
                    'R_pct': r_pct,
                    'D_pct': d_pct,
                    'margin': margin,
                    'towns_found': towns_found
                }
                
                # Check for crossover victories
                dist_info = yearly_district_info[year].get(dist_key, {})
                winners = dist_info.get('winners', [])
        
        # Calculate district's inherent lean across contested years
        contested_years = []
        for year, results in yearly_results.items():
            dist_info = yearly_district_info[year].get(dist_key, {})
            if dist_info.get('contested', False):
                # Adjust for year effect
                expected_r = 50 + (yearly_baselines[year] / 2)
                actual_r = dist_info['r_pct']
                district_performance = actual_r - expected_r
                contested_years.append({
                    'year': year,
                    'performance': district_performance,
                    'margin': results['margin']
                })
                yearly_margins.append(results['margin'])
        
        # Calculate district inherent lean (THE KEY PVI METRIC!)
        if contested_years:
            inherent_lean = np.mean([cy['performance'] for cy in contested_years])
        else:
            # No contested years - use raw data average
            total_r = sum(yr['R'] for yr in yearly_results.values())
            total_d = sum(yr['D'] for yr in yearly_results.values())
            if total_r + total_d > 0:
                inherent_lean = ((total_r / (total_r + total_d)) * 100) - 50
            else:
                inherent_lean = 0
        
        # Check for crossovers - simply districts where both parties have won in 2022-2024
        parties_won_2022_2024 = set()
        for year in [2022, 2024]:
            if year in yearly_results:
                dist_info = yearly_district_info[year].get(dist_key, {})
                winners = dist_info.get('winners', [])
                for party in winners:
                    if party in ['R', 'D']:
                        parties_won_2022_2024.add(party)
        
        # If both R and D have won in 2022-2024, it's a crossover district
        is_crossover = 'R' in parties_won_2022_2024 and 'D' in parties_won_2022_2024
        
        # Calculate volatility metrics
        if len(yearly_margins) > 1:
            margin_swings = []
            for i in range(1, len(yearly_margins)):
                swing = abs(yearly_margins[i] - yearly_margins[i-1])
                margin_swings.append(swing)
            avg_swing = np.mean(margin_swings)
            max_swing = max(margin_swings) if margin_swings else 0
        else:
            avg_swing = 0
            max_swing = 0
        
        # Sophisticated competitiveness analysis
        is_competitive = False
        competitive_reasons = []
        
        # 1. Close inherent lean (true swing districts)
        if abs(inherent_lean) < 5:
            is_competitive = True
            competitive_reasons.append("close_margin")
        
        # 2. Moderate lean with high volatility
        elif abs(inherent_lean) < 10 and avg_swing > 8:
            is_competitive = True
            competitive_reasons.append("volatile")
        
        # 3. Any crossover victories (both parties won in 2022-2024)
        if is_crossover:
            is_competitive = True
            competitive_reasons.append("crossover")
        
        # 4. Different outcomes in different environments
        # Check if district would flip in different year scenarios
        neutral_result = inherent_lean  # R+0 year
        d_wave_result = inherent_lean - 5  # D+5 year
        r_wave_result = inherent_lean + 5  # R+5 year
        
        if (neutral_result > 0 and d_wave_result < 0) or (neutral_result < 0 and r_wave_result > 0):
            is_competitive = True
            competitive_reasons.append("environment_sensitive")
        
        # Format PVI label based on inherent lean
        if inherent_lean > 0:
            pvi_label = f"R+{int(round(abs(inherent_lean)))}"
        elif inherent_lean < 0:
            pvi_label = f"D+{int(round(abs(inherent_lean)))}"
        else:
            pvi_label = "EVEN"
        
        # Classify district rating in different environments
        def get_rating(lean, environment_adjust=0):
            """Get district rating based on lean + environment"""
            adjusted = lean + environment_adjust
            abs_adjusted = abs(adjusted)
            
            if abs_adjusted < 3:
                return "Toss-up"
            elif abs_adjusted < 6:
                return f"Tilt {'R' if adjusted > 0 else 'D'}"
            elif abs_adjusted < 10:
                return f"Lean {'R' if adjusted > 0 else 'D'}"
            elif abs_adjusted < 15:
                return f"Likely {'R' if adjusted > 0 else 'D'}"
            else:
                return f"Safe {'R' if adjusted > 0 else 'D'}"
        
        # Get seat count
        seats = get_seat_count(county, district)
        
        # Count elections
        elections_analyzed = len(yearly_results)
        contested_elections = len(contested_years)
        
        district_results.append({
            'county': county,
            'district': district,
            'seats': seats,
            'town_count': len(towns),
            'towns': ', '.join(sorted(towns)),
            # Core PVI metrics
            'pvi': round(inherent_lean, 1),  # THIS IS THE KEY METRIC
            'pvi_label': pvi_label,
            # Competitiveness metrics
            'is_competitive': bool(is_competitive),
            'competitive_reasons': ', '.join(competitive_reasons) if competitive_reasons else '',
            'is_crossover': is_crossover,
            'avg_swing': round(avg_swing, 1),
            'max_swing': round(max_swing, 1),
            # Electoral environment ratings
            'rating_neutral': get_rating(inherent_lean, 0),
            'rating_d5': get_rating(inherent_lean, -5),  # D+5 environment
            'rating_r5': get_rating(inherent_lean, 5),   # R+5 environment
            # Election history
            'elections_analyzed': elections_analyzed,
            'contested_elections': contested_elections,
            # Recent performance (2022 & 2024)
            'margin_2022': round(yearly_results.get(2022, {}).get('margin', 0), 1),
            'margin_2024': round(yearly_results.get(2024, {}).get('margin', 0), 1),
            # Full history for detailed analysis
            'yearly_results': yearly_results,
            'crossover_details': crossover_elections
        })
    
    # Sort by county and district
    district_results.sort(key=lambda x: (x['county'], int(x['district'])))
    
    return district_results, yearly_baselines

def generate_final_pvi_report(district_results, yearly_baselines):
    """Generate comprehensive final PVI report"""
    
    # Write to CSV
    with open('nh_house_pvi_final.csv', 'w', newline='') as f:
        fieldnames = ['county', 'district', 'seats', 'town_count', 'towns', 
                      'pvi', 'pvi_label', 'is_competitive', 'competitive_reasons',
                      'is_crossover', 'avg_swing', 'max_swing',
                      'rating_neutral', 'rating_d5', 'rating_r5',
                      'elections_analyzed', 'contested_elections',
                      'margin_2022', 'margin_2024']
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(district_results)
    
    print("\n" + "="*80)
    print("NEW HAMPSHIRE HOUSE DISTRICTS - FINAL PVI ANALYSIS")
    print("PVI = District Inherent Lean (Performance vs State Average)")
    print("="*80)
    
    print("\nYearly Political Environments:")
    for year in sorted(yearly_baselines.keys()):
        tilt = yearly_baselines[year]
        party = "R" if tilt > 0 else "D"
        print(f"  {year}: {party}+{abs(tilt):.1f}")
    
    # Count districts by rating in neutral environment
    ratings = {}
    for dist in district_results:
        rating = dist['rating_neutral']
        ratings[rating] = ratings.get(rating, 0) + 1
    
    print(f"\nDistrict Ratings in Neutral Environment (R+0):")
    for rating in ['Safe R', 'Likely R', 'Lean R', 'Tilt R', 'Toss-up', 'Tilt D', 'Lean D', 'Likely D', 'Safe D']:
        if rating in ratings:
            seats = sum(d['seats'] for d in district_results if d['rating_neutral'] == rating)
            print(f"  {rating:10s}: {ratings[rating]:3d} districts ({seats:3d} seats)")
    
    # Competitive districts
    competitive = [d for d in district_results if d['is_competitive']]
    print(f"\n{len(competitive)} Competitive Districts ({sum(d['seats'] for d in competitive)} seats):")
    
    # Group by reason
    reasons = defaultdict(list)
    for dist in competitive:
        for reason in dist['competitive_reasons'].split(', '):
            if reason:
                reasons[reason].append(dist)
    
    print("\nCompetitive by Type:")
    print(f"  Close Margin (PVI < 5):      {len(reasons.get('close_margin', []))} districts")
    print(f"  High Volatility:             {len(reasons.get('volatile', []))} districts")
    print(f"  Crossover History:           {len(reasons.get('crossover', []))} districts")
    print(f"  Environment Sensitive:       {len(reasons.get('environment_sensitive', []))} districts")
    
    # Most competitive districts
    print("\nMost Competitive Districts:")
    competitive_sorted = sorted(competitive, key=lambda x: (abs(x['pvi']), x['is_crossover']), reverse=False)
    for dist in competitive_sorted[:20]:
        crossover = " *" if dist['is_crossover'] else ""
        print(f"  {dist['county']}-{dist['district']:>2s}: {dist['pvi_label']:5s} " +
              f"(swing: {dist['avg_swing']:4.1f}) {dist['competitive_reasons']}{crossover}")
    
    # Environmental sensitivity analysis
    print("\nDistricts That Flip Based on Political Environment:")
    flippers = []
    for dist in district_results:
        if dist['rating_neutral'] != dist['rating_d5'] or dist['rating_neutral'] != dist['rating_r5']:
            if 'Safe' not in dist['rating_neutral']:  # Exclude safe seats that just change degree
                flippers.append(dist)
    
    print(f"\n{len(flippers)} districts change rating based on environment:")
    for dist in sorted(flippers, key=lambda x: abs(x['pvi']))[:15]:
        print(f"  {dist['county']}-{dist['district']:>2s} ({dist['pvi_label']:5s}): " +
              f"{dist['rating_d5']:8s} → {dist['rating_neutral']:8s} → {dist['rating_r5']:8s}")
    
    # Strategic seat counts
    print("\nStrategic Seat Analysis:")
    
    # In different environments
    for env_name, rating_key in [("D+5 Wave", "rating_d5"), ("Neutral", "rating_neutral"), ("R+5 Wave", "rating_r5")]:
        print(f"\n{env_name} Environment:")
        r_seats = sum(d['seats'] for d in district_results if 'R' in d[rating_key] and 'Toss' not in d[rating_key])
        d_seats = sum(d['seats'] for d in district_results if 'D' in d[rating_key] and 'Toss' not in d[rating_key])
        tossup = sum(d['seats'] for d in district_results if 'Toss' in d[rating_key])
        print(f"  Likely R: {r_seats:3d} seats")
        print(f"  Toss-up:  {tossup:3d} seats")
        print(f"  Likely D: {d_seats:3d} seats")
        print(f"  Total:    {r_seats + tossup + d_seats:3d} seats")
    
    # Save detailed results
    with open('nh_house_pvi_final.json', 'w') as f:
        # Remove the yearly results for JSON serialization
        json_data = []
        for dist in district_results:
            dist_copy = dist.copy()
            dist_copy.pop('yearly_results', None)
            dist_copy.pop('crossover_details', None)
            json_data.append(dist_copy)
        json.dump(json_data, f, indent=2)
    
    print(f"\nResults saved to:")
    print(f"  - nh_house_pvi_final.csv (strategic analysis)")
    print(f"  - nh_house_pvi_final.json (detailed data)")

if __name__ == "__main__":
    # Calculate final PVI
    district_results, yearly_baselines = calculate_district_pvi_final()
    
    # Generate report
    generate_final_pvi_report(district_results, yearly_baselines)