#!/usr/bin/env python3
"""
Convert winner JSON files to CSV format for 2016, 2018, 2020
"""

import json
import csv

def convert_winners_json_to_csv(year):
    """Convert JSON winner file to CSV format matching 2022/2024 structure"""
    
    # Read JSON file
    with open(f'nh_election_data/{year}_winners.json', 'r') as f:
        data = json.load(f)
    
    # Prepare CSV rows
    rows = []
    
    for district_key, district_data in data.items():
        # Parse county and district from key
        parts = district_key.split()
        county = parts[0]
        district = parts[1]
        
        # Extract winners
        for winner in district_data['winners']:
            rows.append({
                'year': year,
                'county': county,
                'district': int(district),
                'candidate': winner['candidate'],
                'party': winner['party']
            })
    
    # Write to CSV
    output_file = f'{year}_nh_winners_comprehensive.csv'
    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['year', 'county', 'district', 'candidate', 'party'])
        writer.writeheader()
        writer.writerows(rows)
    
    # Count party totals
    r_count = sum(1 for row in rows if row['party'] == 'R')
    d_count = sum(1 for row in rows if row['party'] == 'D')
    other_count = len(rows) - r_count - d_count
    
    print(f"{year}: Total={len(rows)}, R={r_count}, D={d_count}, Other={other_count}")
    
    return len(rows), r_count, d_count, other_count

# Convert all three years
print("Converting winner JSON files to CSV...")
totals = {}
for year in [2016, 2018, 2020]:
    total, r, d, other = convert_winners_json_to_csv(year)
    totals[year] = {'total': total, 'R': r, 'D': d, 'Other': other}

print("\nSummary:")
for year, counts in totals.items():
    print(f"{year}: {counts}")