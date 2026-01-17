#!/usr/bin/env python3
"""
Thoroughly examine every single 2022 file to understand data structure
"""

import pandas as pd
import glob
import re

def examine_file_structure(filepath):
    county = re.search(r'house-([a-z]+)', filepath).group(1).title()
    df = pd.read_excel(filepath)
    
    print(f"\n{'='*80}")
    print(f"EXAMINING {county.upper()} COUNTY")
    print(f"File: {filepath}")
    print(f"Shape: {df.shape}")
    print(f"{'='*80}")
    
    districts_found = []
    
    # Go through every single row
    for row in range(len(df)):
        first_col = str(df.iloc[row, 0]) if pd.notna(df.iloc[row, 0]) else ''
        
        # Look for district patterns
        if 'district' in first_col.lower():
            print(f"\nRow {row:3d}: {first_col}")
            
            # Show the next 10 rows to understand structure
            for i in range(1, 11):
                if row + i >= len(df):
                    break
                next_row_data = []
                for col in range(min(10, len(df.columns))):
                    val = df.iloc[row + i, col]
                    if pd.notna(val) and str(val).strip():
                        next_row_data.append(f"[{col}]{val}")
                if next_row_data:
                    print(f"     +{i:2d}: {' | '.join(next_row_data[:6])}")
                else:
                    print(f"     +{i:2d}: [empty]")
            
            # Extract district info
            if '(' in first_col and ')' in first_col:
                seat_match = re.search(r'\((\d+)\)', first_col)
                dist_match = re.search(r'District\s+(?:No\.?\s*)?(\d+)', first_col)
                if seat_match and dist_match:
                    dist_num = dist_match.group(1)
                    seats = int(seat_match.group(1))
                    districts_found.append((dist_num, seats))
    
    print(f"\nSUMMARY FOR {county}:")
    print(f"Districts found: {len(districts_found)}")
    total_seats = sum(seats for _, seats in districts_found)
    print(f"Total seats: {total_seats}")
    
    if districts_found:
        print("District details:")
        for dist_num, seats in districts_found:
            print(f"  District {dist_num}: {seats} seats")
    
    return len(districts_found), total_seats

# Examine all files
files = sorted(glob.glob('nh_election_data/*2022*.xls*'))
total_districts = 0
total_seats = 0

for filepath in files:
    districts, seats = examine_file_structure(filepath)
    total_districts += districts
    total_seats += seats

print(f"\n{'='*80}")
print("OVERALL SUMMARY")
print(f"{'='*80}")
print(f"Total files examined: {len(files)}")
print(f"Total districts found: {total_districts}")
print(f"Total seats found: {total_seats}")
print(f"Expected: 203 districts, 400 seats")
print(f"Missing: {203 - total_districts} districts, {400 - total_seats} seats")