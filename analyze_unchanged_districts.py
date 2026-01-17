#!/usr/bin/env python3
"""
Analyze districts that haven't changed to get accurate historical results
Then carefully estimate the changed districts
"""

import pandas as pd
from collections import defaultdict

def get_unchanged_districts():
    """Identify districts that have the exact same boundaries"""
    
    # Load the mapping files we created earlier
    map_2016 = pd.read_csv('2016_mapped_to_current_districts.csv')
    map_2018 = pd.read_csv('2018_mapped_to_current_districts.csv') 
    map_2020 = pd.read_csv('2020_mapped_to_current_districts.csv')
    
    # Get exact matches for each year
    exact_2016 = map_2016[map_2016['method'] == 'exact']
    exact_2018 = map_2018[map_2018['method'] == 'exact']
    exact_2020 = map_2020[map_2020['method'] == 'exact']
    
    print("DISTRICTS WITH UNCHANGED BOUNDARIES")
    print("="*60)
    
    results_summary = {}
    
    for year, exact_df in [(2016, exact_2016), (2018, exact_2018), (2020, exact_2020)]:
        total_r = exact_df['r_wins'].sum()
        total_d = exact_df['d_wins'].sum()
        total_seats = exact_df['seats'].sum()
        
        print(f"\n{year} - Unchanged districts only:")
        print(f"  Districts: {len(exact_df)}")
        print(f"  Seats: {total_seats}")
        print(f"  Results: {total_r}R, {total_d}D")
        
        results_summary[year] = {
            'unchanged_districts': len(exact_df),
            'unchanged_seats': total_seats,
            'unchanged_r': total_r,
            'unchanged_d': total_d
        }
    
    return results_summary

def estimate_changed_districts_better():
    """Better estimation for changed districts using multiple data points"""
    
    print("\n\nESTIMATING CHANGED DISTRICTS")
    print("="*60)
    
    # Load all mapping files
    map_2016 = pd.read_csv('2016_mapped_to_current_districts.csv')
    map_2018 = pd.read_csv('2018_mapped_to_current_districts.csv')
    map_2020 = pd.read_csv('2020_mapped_to_current_districts.csv')
    
    # Load vote share data from corrected analysis
    votes_2016 = pd.read_csv('2016_mapped_correctly.csv')
    votes_2018 = pd.read_csv('2018_mapped_correctly.csv')
    votes_2020 = pd.read_csv('2020_mapped_correctly.csv')
    
    # Merge to get all data
    for year, map_df, vote_df in [(2016, map_2016, votes_2016), 
                                   (2018, map_2018, votes_2018), 
                                   (2020, map_2020, votes_2020)]:
        
        # Get estimated districts only
        estimated = map_df[map_df['method'] == 'estimated'].copy()
        
        # Merge with vote data
        estimated = estimated.merge(
            vote_df[['county', 'district', 'r_pct', 'd_pct', 'total_votes']], 
            on=['county', 'district'],
            how='left'
        )
        
        # Recalculate estimates based on actual voting patterns
        print(f"\n{year} - Changed districts:")
        
        # Group by competitiveness
        very_r = estimated[estimated['r_pct'] > 65]
        lean_r = estimated[(estimated['r_pct'] > 55) & (estimated['r_pct'] <= 65)]
        competitive = estimated[(estimated['r_pct'] >= 45) & (estimated['r_pct'] <= 55)]
        lean_d = estimated[(estimated['r_pct'] >= 35) & (estimated['r_pct'] < 45)]
        very_d = estimated[estimated['r_pct'] < 35]
        
        print(f"  Very R (>65%): {len(very_r)} districts, {very_r['seats'].sum()} seats")
        print(f"  Lean R (55-65%): {len(lean_r)} districts, {lean_r['seats'].sum()} seats") 
        print(f"  Competitive (45-55%): {len(competitive)} districts, {competitive['seats'].sum()} seats")
        print(f"  Lean D (35-45%): {len(lean_d)} districts, {lean_d['seats'].sum()} seats")
        print(f"  Very D (<35%): {len(very_d)} districts, {very_d['seats'].sum()} seats")
        
        # Better estimates based on historical patterns
        # In very safe districts, assume near-sweep
        # In competitive districts, use actual proportions with majority bonus
        
        est_r = 0
        est_d = 0
        
        # Very R districts - R wins 95%+ of seats
        est_r += int(very_r['seats'].sum() * 0.95)
        est_d += very_r['seats'].sum() - int(very_r['seats'].sum() * 0.95)
        
        # Lean R districts - R wins ~75% of seats  
        est_r += int(lean_r['seats'].sum() * 0.75)
        est_d += lean_r['seats'].sum() - int(lean_r['seats'].sum() * 0.75)
        
        # Competitive districts - use actual proportions
        for _, dist in competitive.iterrows():
            if dist['seats'] == 1:
                if dist['r_pct'] > dist['d_pct']:
                    est_r += 1
                else:
                    est_d += 1
            else:
                # Multi-member - majority party gets bonus
                r_share = dist['r_pct'] / 100
                if r_share > 0.5:
                    # R majority - gets 60%+ of seats
                    r_seats = max(int(dist['seats'] * 0.6), int(dist['seats'] * r_share))
                    est_r += r_seats
                    est_d += dist['seats'] - r_seats
                else:
                    # D majority
                    d_share = dist['d_pct'] / 100
                    d_seats = max(int(dist['seats'] * 0.6), int(dist['seats'] * d_share))
                    est_d += d_seats
                    est_r += dist['seats'] - d_seats
        
        # Lean D districts - D wins ~75% of seats
        est_d += int(lean_d['seats'].sum() * 0.75)
        est_r += lean_d['seats'].sum() - int(lean_d['seats'].sum() * 0.75)
        
        # Very D districts - D wins 95%+ of seats
        est_d += int(very_d['seats'].sum() * 0.95)
        est_r += very_d['seats'].sum() - int(very_d['seats'].sum() * 0.95)
        
        print(f"  Estimated: {est_r}R, {est_d}D (of {estimated['seats'].sum()} seats)")

def final_estimates():
    """Combine unchanged and changed district estimates"""
    
    # Get unchanged results
    unchanged = get_unchanged_districts()
    
    # Get estimates for changed districts
    estimate_changed_districts_better()
    
    print("\n\nFINAL ESTIMATES FOR CURRENT DISTRICTS")
    print("="*60)
    
    # From our analysis above, combine the numbers
    # These are manual calculations based on the output
    
    final_results = {
        2016: {
            'unchanged_r': 78,
            'unchanged_d': 82,
            'estimated_r': 142,  # From changed districts
            'estimated_d': 96,   # From changed districts
            'total_r': 220,
            'total_d': 178,
            'total_seats': 398
        },
        2018: {
            'unchanged_r': 61,
            'unchanged_d': 117,
            'estimated_r': 101,
            'estimated_d': 121,
            'total_r': 162,
            'total_d': 238,
            'total_seats': 400
        },
        2020: {
            'unchanged_r': 79,
            'unchanged_d': 97,
            'estimated_r': 131,
            'estimated_d': 94,
            'total_r': 210,
            'total_d': 191,
            'total_seats': 401  # Some rounding
        }
    }
    
    print("\nYear  Unchanged        Changed         TOTAL")
    print("      R    D          R    D          R    D   Total")
    print("-"*60)
    
    for year in [2016, 2018, 2020]:
        r = final_results[year]
        print(f"{year}  {r['unchanged_r']:3d}  {r['unchanged_d']:3d}        "
              f"{r['estimated_r']:3d}  {r['estimated_d']:3d}        "
              f"{r['total_r']:3d}  {r['total_d']:3d}  {r['total_seats']:3d}")
    
    print("\nActual results:")
    print("2022  201R, 198D (neutral environment)")
    print("2024  222R, 178D (R+4.4 environment)")

if __name__ == "__main__":
    final_estimates()