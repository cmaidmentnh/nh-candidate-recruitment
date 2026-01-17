#!/usr/bin/env python3
"""
Correctly map historical election results to current district boundaries
Handle cases where districts have same towns but different seat counts
"""

import pandas as pd
import json
from collections import defaultdict

def load_current_districts():
    """Load current district definitions from 2022/2024"""
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

def get_historical_results_by_town(year):
    """Get vote totals by town and party for a given year"""
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
    
    return dict(town_votes)

def map_votes_to_current_districts(year):
    """Map historical votes to current district boundaries"""
    print(f"\n{'='*80}")
    print(f"Mapping {year} results to current districts")
    print('='*80)
    
    current_districts = load_current_districts()
    town_votes = get_historical_results_by_town(year)
    
    district_results = []
    total_r_seats = 0
    total_d_seats = 0
    
    for dist_key, dist_info in sorted(current_districts.items()):
        county = dist_info['county']
        district = dist_info['district']
        seats = dist_info['seats']
        towns = dist_info['towns']
        
        # Aggregate votes for this district
        district_votes = {'R': 0, 'D': 0, 'Other': 0}
        towns_found = []
        towns_missing = []
        
        for town in towns:
            if town in town_votes:
                district_votes['R'] += town_votes[town]['R']
                district_votes['D'] += town_votes[town]['D']
                district_votes['Other'] += town_votes[town]['Other']
                towns_found.append(town)
            else:
                towns_missing.append(town)
        
        # Calculate seat allocation
        total_votes = district_votes['R'] + district_votes['D'] + district_votes['Other']
        
        if total_votes > 0:
            r_pct = district_votes['R'] / total_votes
            d_pct = district_votes['D'] / total_votes
            
            # For single-member districts, winner takes all
            if seats == 1:
                if r_pct > d_pct:
                    r_seats = 1
                    d_seats = 0
                else:
                    r_seats = 0
                    d_seats = 1
            else:
                # For multi-member districts, use a more sophisticated allocation
                # that accounts for typical voting patterns
                
                # Start with proportional allocation
                r_seats_raw = seats * r_pct
                d_seats_raw = seats * d_pct
                
                # Apply rounding that favors the majority party (common in at-large elections)
                if r_pct > d_pct:
                    # R majority - they likely win more than proportional share
                    r_seats = int(r_seats_raw + 0.4)  # Round up more easily
                    d_seats = seats - r_seats
                else:
                    # D majority
                    d_seats = int(d_seats_raw + 0.4)
                    r_seats = seats - d_seats
                
                # Ensure we don't exceed seat count
                r_seats = min(r_seats, seats)
                d_seats = min(d_seats, seats)
                
                # If one party has overwhelming majority (>65%), they might sweep
                if r_pct > 0.65:
                    r_seats = seats
                    d_seats = 0
                elif d_pct > 0.65:
                    d_seats = seats
                    r_seats = 0
        else:
            # No data found
            r_seats = 0
            d_seats = 0
            r_pct = 0
            d_pct = 0
        
        district_results.append({
            'county': county,
            'district': district,
            'seats': seats,
            'r_seats': r_seats,
            'd_seats': d_seats,
            'r_votes': district_votes['R'],
            'd_votes': district_votes['D'],
            'total_votes': total_votes,
            'r_pct': r_pct * 100,
            'd_pct': d_pct * 100,
            'towns_found': len(towns_found),
            'towns_total': len(towns),
            'towns_missing': ', '.join(towns_missing) if towns_missing else ''
        })
        
        total_r_seats += r_seats
        total_d_seats += d_seats
    
    # Create summary
    print(f"\n{year} Results Summary:")
    print(f"  Total R seats: {total_r_seats}")
    print(f"  Total D seats: {total_d_seats}")
    print(f"  Total seats: {total_r_seats + total_d_seats}")
    
    # Show sample results
    print(f"\nSample district results:")
    df = pd.DataFrame(district_results)
    sample = df[df['total_votes'] > 0].head(10)
    for _, row in sample.iterrows():
        print(f"  {row['county']}-{row['district']}: {row['r_seats']}R, {row['d_seats']}D "
              f"(of {row['seats']} seats) - {row['r_pct']:.1f}% R, {row['d_pct']:.1f}% D")
    
    # Save results
    output_file = f'{year}_mapped_correctly.csv'
    df.to_csv(output_file, index=False)
    print(f"\nDetailed results saved to {output_file}")
    
    return total_r_seats, total_d_seats

def main():
    """Map all historical years to current districts"""
    
    results_summary = {}
    
    for year in [2016, 2018, 2020, 2022, 2024]:
        try:
            r_seats, d_seats = map_votes_to_current_districts(year)
            results_summary[year] = {
                'R': r_seats,
                'D': d_seats,
                'total': r_seats + d_seats
            }
        except Exception as e:
            print(f"Error processing {year}: {e}")
    
    # Final summary
    print(f"\n{'='*80}")
    print("Historical Results Mapped to Current Districts")
    print('='*80)
    print(f"{'Year':<6} {'R Seats':<10} {'D Seats':<10} {'Total':<8}")
    print('-'*40)
    
    for year in sorted(results_summary.keys()):
        r = results_summary[year]['R']
        d = results_summary[year]['D']
        total = results_summary[year]['total']
        print(f"{year:<6} {r:<10} {d:<10} {total:<8}")
    
    # Calculate environmental baselines
    print("\nEnvironmental Analysis:")
    if 2022 in results_summary and 2024 in results_summary:
        # 2022 was neutral (D+0.3)
        # 2024 was R+4.4
        neutral_r = results_summary[2022]['R']
        neutral_total = results_summary[2022]['total']
        r_plus_4 = results_summary[2024]['R']
        
        seats_per_point = (r_plus_4 - neutral_r) / 4.4
        print(f"  2022 (neutral): {neutral_r}R of {neutral_total} = {neutral_r/neutral_total*100:.1f}%")
        print(f"  2024 (R+4.4): {r_plus_4}R of {results_summary[2024]['total']} = {r_plus_4/results_summary[2024]['total']*100:.1f}%")
        print(f"  Seats per environment point: {seats_per_point:.1f}")

if __name__ == "__main__":
    main()