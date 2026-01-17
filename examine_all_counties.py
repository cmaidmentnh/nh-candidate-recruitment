#!/usr/bin/env python3
"""
Examine all county files to understand complete data structures
"""

import pandas as pd
import glob
import re

def examine_county(filepath):
    county = re.search(r'house-([a-z]+)', filepath).group(1).title()
    df = pd.read_excel(filepath)
    
    print(f"\n{'='*80}")
    print(f"COUNTY: {county}")
    print(f"{'='*80}")
    print(f"Shape: {df.shape}")
    
    # Find all district headers
    districts = []
    for row in range(len(df)):
        first_col = str(df.iloc[row, 0]) if pd.notna(df.iloc[row, 0]) else ''
        if 'District' in first_col and ('(' in first_col or 'No.' in first_col):
            # Extract info
            dist_match = re.search(r'District.*?(\d+)', first_col)
            seat_match = re.search(r'\((\d+)\)', first_col)
            
            if dist_match:
                dist_num = dist_match.group(1)
                seats = int(seat_match.group(1)) if seat_match else '?'
                
                # Look at the row content
                row_content = []
                for col in range(1, min(10, len(df.columns))):
                    val = df.iloc[row, col]
                    if pd.notna(val) and str(val).strip():
                        row_content.append(str(val))
                
                districts.append({
                    'row': row,
                    'district': dist_num,
                    'seats': seats,
                    'header': first_col.strip(),
                    'row_content': row_content[:5]  # First 5 non-empty values
                })
    
    print(f"\nFound {len(districts)} districts:")
    for d in districts:
        print(f"  Row {d['row']:3d}: District {d['district']:>2s} ({d['seats']} seats) - {d['row_content']}")
    
    # Look for patterns in missing districts
    if county == "Grafton":
        print("\nChecking Grafton for missing districts...")
        # Look between districts 3 and 5
        print("\nRows 20-35 (between District 3 and 5):")
        for row in range(20, 35):
            first_col = str(df.iloc[row, 0]) if pd.notna(df.iloc[row, 0]) else ''
            if first_col.strip():
                print(f"  Row {row}: {first_col}")
    
    return districts

# Main execution
all_districts = {}
files = sorted(glob.glob('nh_election_data/*2022*.xls*'))

for filepath in files:
    districts = examine_county(filepath)
    county = re.search(r'house-([a-z]+)', filepath).group(1).title()
    all_districts[county] = districts

# Summary
print(f"\n{'='*80}")
print("SUMMARY BY COUNTY")
print(f"{'='*80}")
total_districts = 0
total_seats = 0

for county in sorted(all_districts.keys()):
    districts = all_districts[county]
    county_seats = sum(d['seats'] for d in districts if isinstance(d['seats'], int))
    total_districts += len(districts)
    total_seats += county_seats
    print(f"{county:15s}: {len(districts):3d} districts, {county_seats:3d} seats")

print(f"\nTotal: {total_districts} districts, {total_seats} seats")
print(f"Expected: 203 districts, 400 seats")
print(f"Missing: {203 - total_districts} districts, {400 - total_seats} seats")