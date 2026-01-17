#!/usr/bin/env python3
"""
Generate comprehensive PVI summary report from accurate data
"""

import pandas as pd

def generate_report():
    """Generate PVI summary report"""
    print("\n" + "="*80)
    print("NEW HAMPSHIRE HOUSE DISTRICTS - ACCURATE PVI ANALYSIS")
    print("Based on 2016-2024 Elections with Current District Boundaries")
    print("="*80)
    
    # Read the accurate PVI data
    df = pd.read_csv('nh_house_pvi_accurate.csv')
    
    # Calculate district counts by PVI category
    safe_r = len(df[df['pvi_raw'] >= 15])
    likely_r = len(df[(df['pvi_raw'] >= 10) & (df['pvi_raw'] < 15)])
    lean_r = len(df[(df['pvi_raw'] >= 5) & (df['pvi_raw'] < 10)])
    tilt_r = len(df[(df['pvi_raw'] > 0) & (df['pvi_raw'] < 5)])
    tilt_d = len(df[(df['pvi_raw'] > -5) & (df['pvi_raw'] <= 0)])
    lean_d = len(df[(df['pvi_raw'] > -10) & (df['pvi_raw'] <= -5)])
    likely_d = len(df[(df['pvi_raw'] > -15) & (df['pvi_raw'] <= -10)])
    safe_d = len(df[df['pvi_raw'] <= -15])
    
    print(f"\nDistrict Classifications ({len(df)} districts analyzed):")
    print(f"  Safe Republican (R+15 or more):     {safe_r:3d} districts")
    print(f"  Likely Republican (R+10 to R+14):   {likely_r:3d} districts")
    print(f"  Lean Republican (R+5 to R+9):       {lean_r:3d} districts")
    print(f"  Tilt Republican (R+1 to R+4):       {tilt_r:3d} districts")
    print(f"  Tilt Democratic (D+0 to D+4):       {tilt_d:3d} districts")
    print(f"  Lean Democratic (D+5 to D+9):       {lean_d:3d} districts")
    print(f"  Likely Democratic (D+10 to D+14):   {likely_d:3d} districts")
    print(f"  Safe Democratic (D+15 or more):     {safe_d:3d} districts")
    
    # Calculate seat counts by PVI category
    safe_r_seats = df[df['pvi_raw'] >= 15]['seats'].sum()
    likely_r_seats = df[(df['pvi_raw'] >= 10) & (df['pvi_raw'] < 15)]['seats'].sum()
    lean_r_seats = df[(df['pvi_raw'] >= 5) & (df['pvi_raw'] < 10)]['seats'].sum()
    tilt_r_seats = df[(df['pvi_raw'] > 0) & (df['pvi_raw'] < 5)]['seats'].sum()
    tilt_d_seats = df[(df['pvi_raw'] > -5) & (df['pvi_raw'] <= 0)]['seats'].sum()
    lean_d_seats = df[(df['pvi_raw'] > -10) & (df['pvi_raw'] <= -5)]['seats'].sum()
    likely_d_seats = df[(df['pvi_raw'] > -15) & (df['pvi_raw'] <= -10)]['seats'].sum()
    safe_d_seats = df[df['pvi_raw'] <= -15]['seats'].sum()
    
    total_seats = df['seats'].sum()
    
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
    print("\nMost Competitive Districts (smallest partisan lean):")
    competitive = df.nsmallest(20, 'pvi_raw', keep='all').sort_values('pvi_raw', key=abs)
    for _, dist in competitive.head(15).iterrows():
        print(f"  {dist['county']}-{int(dist['district'])}: {dist['pvi_label']:5s} " +
              f"(R: {dist['r_vote_pct']:4.1f}%, D: {dist['d_vote_pct']:4.1f}%)")
    
    # County summaries
    print("\nCounty-Level Summary:")
    counties = df.groupby('county').agg({
        'pvi_raw': 'mean',
        'seats': 'sum',
        'total_votes': 'sum'
    }).sort_values('pvi_raw', ascending=False)
    
    for county, data in counties.iterrows():
        county_districts = df[df['county'] == county]
        r_districts = len(county_districts[county_districts['pvi_raw'] > 0])
        d_districts = len(county_districts[county_districts['pvi_raw'] < 0])
        even_districts = len(county_districts[county_districts['pvi_raw'] == 0])
        
        avg_pvi = data['pvi_raw']
        if avg_pvi > 0:
            lean = f"R+{int(round(abs(avg_pvi)))}"
        elif avg_pvi < 0:
            lean = f"D+{int(round(abs(avg_pvi)))}"
        else:
            lean = "EVEN"
        
        print(f"  {county:12s}: {lean:5s} " +
              f"(R: {r_districts}, D: {d_districts}, Even: {even_districts})")
    
    # Compare to actual 2022 and 2024 results
    print("\nComparison to Actual Election Results:")
    
    # Expected seats based on PVI
    expected_r = safe_r_seats + likely_r_seats + lean_r_seats + (tilt_r_seats * 0.7) + (tilt_d_seats * 0.3)
    expected_d = safe_d_seats + likely_d_seats + lean_d_seats + (tilt_d_seats * 0.7) + (tilt_r_seats * 0.3)
    
    print(f"\nExpected seats based on PVI:")
    print(f"  Republican: {int(expected_r)} seats")
    print(f"  Democratic: {int(expected_d)} seats")
    
    print(f"\nActual 2022 results: 201R, 198D, 1 Vacant")
    print(f"Actual 2024 results: 222R, 178D")
    
    # Competitive districts analysis
    competitive_districts = df[df['is_competitive'] == True]
    print(f"\n{len(competitive_districts)} districts identified as competitive")
    print(f"These districts control {competitive_districts['seats'].sum()} seats")
    
    # Save detailed competitive districts list
    competitive_sorted = competitive_districts.sort_values('pvi_raw', key=abs)
    competitive_sorted.to_csv('nh_competitive_districts.csv', index=False)
    print("\nCompetitive districts list saved to nh_competitive_districts.csv")

if __name__ == "__main__":
    generate_report()