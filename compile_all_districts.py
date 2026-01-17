#!/usr/bin/env python3
"""
Compile ALL districts: district name, number of seats, towns in district
"""

import pandas as pd
import glob
import csv
import re

def extract_all_districts(filepath):
    county = re.search(r'house-([a-z]+)', filepath).group(1).title()
    df = pd.read_excel(filepath)
    
    districts = {}
    
    for row in range(len(df)):
        first_col = str(df.iloc[row, 0])
        
        # Look for district patterns - be more flexible
        if 'district' in first_col.lower() and '(' in first_col:
            # Extract seats
            seat_match = re.search(r'\((\d+)\)', first_col)
            if not seat_match:
                continue
            seats = int(seat_match.group(1))
            
            # Extract district number - try multiple patterns
            dist_patterns = [
                r'District No\.\s*(\d+)',
                r'District No\s*(\d+)', 
                r'District\s+(\d+)',
                r'Dist\.\s*(\d+)'
            ]
            
            dist_num = None
            for pattern in dist_patterns:
                match = re.search(pattern, first_col)
                if match:
                    dist_num = match.group(1)
                    break
            
            if not dist_num:
                continue
                
            dist_key = f"{county} {dist_num}"
            
            # Initialize district info
            districts[dist_key] = {
                'seats': seats,
                'towns': set()
            }
            
            # Look for towns in following rows until next district
            for scan_row in range(row + 1, len(df)):
                if scan_row >= len(df):
                    break
                    
                scan_first = str(df.iloc[scan_row, 0])
                
                # Stop if we hit another district
                if 'district' in scan_first.lower() and '(' in scan_first:
                    break
                
                # Skip totals and empty rows
                if pd.notna(df.iloc[scan_row, 0]) and scan_first not in ['', 'Totals']:
                    town = scan_first.strip()
                    if town and not town.lower().startswith('district'):
                        districts[dist_key]['towns'].add(town)
    
    return districts

# Process all files
all_districts = {}
files = sorted(glob.glob('nh_election_data/*2022*.xls*'))

for filepath in files:
    print(f"Processing {filepath}")
    county_districts = extract_all_districts(filepath)
    all_districts.update(county_districts)

# Prepare data for CSV
district_data = []
for district, info in sorted(all_districts.items()):
    towns_list = sorted(list(info['towns']))
    district_data.append([
        district,
        info['seats'],
        '; '.join(towns_list)
    ])

# Write to CSV
with open('nh_election_data/2022_all_districts.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['District', 'Seats', 'Towns'])
    writer.writerows(district_data)

print(f"Compiled {len(all_districts)} districts")
print("Saved to nh_election_data/2022_all_districts.csv")

# Verify totals
total_seats = sum(info['seats'] for info in all_districts.values())
print(f"Total districts: {len(all_districts)} (should be 203)")
print(f"Total seats: {total_seats} (should be 400)")