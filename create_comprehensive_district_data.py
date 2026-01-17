#!/usr/bin/env python3
"""
Create comprehensive district data with all requested columns
"""

import pandas as pd
import json
from collections import defaultdict

# First, we need to get the district seats from 2022 or 2024 data
seats_map = {}
df_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
for county in df_2022['county'].unique():
    for district in df_2022[df_2022['county'] == county]['district'].unique():
        key = f"{county}-{district}"
        seats = len(df_2022[(df_2022['county'] == county) & (df_2022['district'] == district)])
        seats_map[key] = seats

# Load current district structure
with open('current_district_structure.json', 'r') as f:
    district_towns = json.load(f)

# Create output data
output_rows = []

# Process each district
for district_key, towns in district_towns.items():
    parts = district_key.split('-')
    county = parts[0]
    district_num = int(parts[1])
    seats = seats_map.get(district_key, 1)  # Default to 1 if not found
    
    # Process each year
    for year in [2016, 2018, 2020, 2022, 2024]:
        # Load the parsed results for this year
        if year < 2022:
            results_file = f'nh_election_data/{year}_parsed_results.csv'
        else:
            results_file = f'{year}_nh_all_results_comprehensive.csv'
        
        try:
            df = pd.read_csv(results_file)
        except:
            continue
            
        # Process each town in this district
        for town in towns:
            # Initialize totals
            r_votes = 0
            d_votes = 0
            other_votes = 0
            r_candidates = set()
            d_candidates = set()
            
            # Find matching rows for this town
            if year < 2022:
                # Old format
                town_rows = df[df['town'] == town]
            else:
                # New format
                town_rows = df[(df['town'] == town) & (df['county'] == county) & (df['district'] == district_num)]
            
            if len(town_rows) == 0:
                # Try alternative formats
                if year < 2022 and ' Ward ' in town:
                    # Try "Laconia Wd 1" format
                    alt_town = town.replace(' Ward ', ' Wd ')
                    town_rows = df[df['town'] == alt_town]
            
            # Sum up votes
            for _, row in town_rows.iterrows():
                if year < 2022:
                    candidate = row['candidate']
                    party = row['party']
                    votes = row['votes']
                else:
                    candidate = row.get('candidate', '')
                    party = row.get('party', '')
                    votes = row.get('votes', 0)
                
                if pd.isna(votes) or votes == 0:
                    continue
                    
                if party == 'R':
                    r_votes += votes
                    if candidate and candidate != '':
                        r_candidates.add(candidate)
                elif party == 'D':
                    d_votes += votes
                    if candidate and candidate != '':
                        d_candidates.add(candidate)
                else:
                    other_votes += votes
            
            # Count candidates
            r_count = len(r_candidates) if r_candidates else 0
            d_count = len(d_candidates) if d_candidates else 0
            
            # Calculate averages
            r_avg = r_votes / r_count if r_count > 0 else 0
            d_avg = d_votes / d_count if d_count > 0 else 0
            
            # Add row
            output_rows.append({
                'county': county,
                'districtNum': district_num,
                'seats': seats,
                'year': year,
                'town': town,
                'total_R': r_votes,
                'total_D': d_votes,
                'total_Other': other_votes,
                'R_candidate_count': r_count,
                'D_candidate_count': d_count,
                'R_avg_votes': r_avg,
                'D_avg_votes': d_avg
            })

# Create dataframe and save
df_output = pd.DataFrame(output_rows)
df_output = df_output.sort_values(['county', 'districtNum', 'year', 'town'])
df_output.to_csv('comprehensive_district_town_data.csv', index=False)

print(f"Created comprehensive_district_town_data.csv with {len(output_rows)} rows")
print(f"Districts covered: {df_output[['county', 'districtNum']].drop_duplicates().shape[0]}")
print(f"Years included: {sorted(df_output['year'].unique())}")