#!/usr/bin/env python3
"""
Map historical election results to current district boundaries.
This script:
1. Extracts current district structure from 2022/2024 data
2. Creates a mapping of districts to towns
3. Aggregates historical R and D votes for current districts
"""

import pandas as pd
import json
from collections import defaultdict

def extract_current_districts():
    """Extract the current district structure from 2022 and 2024 comprehensive results."""
    print("Extracting current district structure...")
    
    # Read 2022 comprehensive results to get district-town mappings
    df_2022 = pd.read_csv('2022_nh_all_results_comprehensive.csv')
    
    # Also read 2024 to verify consistency
    df_2024 = pd.read_csv('2024_nh_all_results_comprehensive.csv')
    
    # Create district to towns mapping
    district_towns = defaultdict(set)
    
    # Process 2022 data
    for _, row in df_2022.iterrows():
        county = row['county']
        district = row['district']
        town = row['town']
        key = f"{county}-{district}"
        district_towns[key].add(town)
    
    # Verify with 2024 data
    for _, row in df_2024.iterrows():
        county = row['county']
        district = row['district']
        town = row['town']
        key = f"{county}-{district}"
        district_towns[key].add(town)
    
    # Convert sets to sorted lists
    district_towns_dict = {k: sorted(list(v)) for k, v in district_towns.items()}
    
    # Save the mapping
    with open('current_district_structure.json', 'w') as f:
        json.dump(district_towns_dict, f, indent=2)
    
    print(f"Found {len(district_towns_dict)} districts")
    
    # Print summary
    for district, towns in sorted(district_towns_dict.items()):
        print(f"{district}: {len(towns)} towns - {', '.join(towns[:3])}{'...' if len(towns) > 3 else ''}")
    
    return district_towns_dict

def normalize_town_name(town_name):
    """Normalize town names to handle variations in format."""
    # Handle ward abbreviations
    normalized = town_name.replace(" Wd ", " Ward ")
    # Remove special characters like asterisks
    normalized = normalized.replace("*", "")
    # Handle 'Recount' entries
    if normalized == "Recount" or normalized == "Recount Total":
        return None
    return normalized

def aggregate_historical_votes(district_towns_mapping):
    """Aggregate historical votes based on current district boundaries."""
    
    # Create reverse mapping: town -> current district
    town_to_district = {}
    for district, towns in district_towns_mapping.items():
        for town in towns:
            # Store as (county, district_number) for easier matching
            county, district_num = district.split('-')
            town_to_district[f"{county}-{town}"] = (county, district_num)
            # Also store normalized version
            normalized = normalize_town_name(town)
            if normalized:
                town_to_district[f"{county}-{normalized}"] = (county, district_num)
    
    # Process each historical year
    years = [2016, 2018, 2020]
    all_results = []
    unmatched_towns = set()
    
    for year in years:
        print(f"\nProcessing {year} data...")
        
        try:
            df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
            
            # Aggregate votes by current district
            district_votes = defaultdict(lambda: {'R': 0, 'D': 0, 'Other': 0, 'Total': 0})
            
            for _, row in df.iterrows():
                county = row['county']
                town = row['town']
                party = row['party']
                votes = row['votes']
                
                # Normalize town name
                normalized_town = normalize_town_name(town)
                if not normalized_town:
                    continue
                
                # Find current district for this town
                town_key = f"{county}-{normalized_town}"
                if town_key in town_to_district:
                    current_county, current_district = town_to_district[town_key]
                    district_key = f"{current_county}-{current_district}"
                    
                    # Aggregate votes by party
                    if party == 'R':
                        district_votes[district_key]['R'] += votes
                    elif party == 'D':
                        district_votes[district_key]['D'] += votes
                    else:
                        district_votes[district_key]['Other'] += votes
                    
                    district_votes[district_key]['Total'] += votes
                else:
                    unmatched_towns.add(f"{town}, {county}")
            
            # Convert to list of records
            for district, votes in district_votes.items():
                county, district_num = district.split('-')
                all_results.append({
                    'year': year,
                    'county': county,
                    'district': district_num,
                    'R_votes': votes['R'],
                    'D_votes': votes['D'],
                    'Other_votes': votes['Other'],
                    'Total_votes': votes['Total'],
                    'R_percentage': votes['R'] / votes['Total'] * 100 if votes['Total'] > 0 else 0,
                    'D_percentage': votes['D'] / votes['Total'] * 100 if votes['Total'] > 0 else 0
                })
            
            print(f"Processed {len(district_votes)} districts for {year}")
            
        except FileNotFoundError:
            print(f"Warning: {year}_parsed_results.csv not found")
    
    # Print unmatched towns if any
    if unmatched_towns:
        print(f"\nWarning: {len(unmatched_towns)} unique town-county combinations could not be matched:")
        for town in sorted(list(unmatched_towns))[:10]:
            print(f"  - {town}")
        if len(unmatched_towns) > 10:
            print(f"  ... and {len(unmatched_towns) - 10} more")
    
    # Create DataFrame and save
    df_results = pd.DataFrame(all_results)
    df_results = df_results.sort_values(['year', 'county', 'district'])
    df_results.to_csv('historical_elections_in_current_districts_v2.csv', index=False)
    
    # Print summary statistics
    print("\nSummary of historical results in current districts:")
    summary = df_results.groupby('year').agg({
        'R_votes': 'sum',
        'D_votes': 'sum',
        'Total_votes': 'sum'
    })
    summary['R_percentage'] = summary['R_votes'] / summary['Total_votes'] * 100
    summary['D_percentage'] = summary['D_votes'] / summary['Total_votes'] * 100
    print(summary)
    
    return df_results

def analyze_competitive_districts(df_historical):
    """Identify competitive districts based on historical data."""
    
    # Calculate average margins for each district
    district_competitiveness = []
    
    # Get unique districts
    unique_districts = df_historical.groupby(['county', 'district']).size().reset_index()[['county', 'district']]
    
    for _, row in unique_districts.iterrows():
        county = row['county']
        dist_num = row['district']
        
        district_data = df_historical[(df_historical['county'] == county) & 
                                    (df_historical['district'] == dist_num)]
        
        if len(district_data) > 0:
            avg_r_pct = district_data['R_percentage'].mean()
            avg_d_pct = district_data['D_percentage'].mean()
            margin = abs(avg_r_pct - avg_d_pct)
            
            district_competitiveness.append({
                'county': county,
                'district': dist_num,
                'avg_R_pct': avg_r_pct,
                'avg_D_pct': avg_d_pct,
                'margin': margin,
                'lean': 'R' if avg_r_pct > avg_d_pct else 'D',
                'competitive': margin < 10  # Consider <10% margin as competitive
            })
    
    df_competitive = pd.DataFrame(district_competitiveness)
    df_competitive = df_competitive.sort_values('margin')
    
    # Save competitive districts
    competitive_only = df_competitive[df_competitive['competitive']]
    competitive_only.to_csv('competitive_districts_historical.csv', index=False)
    
    print(f"\nFound {len(competitive_only)} competitive districts (margin < 10%):")
    print(competitive_only[['county', 'district', 'margin', 'lean']].head(20))
    
    return df_competitive

def main():
    # Step 1: Extract current district structure
    district_mapping = extract_current_districts()
    
    # Step 2: Aggregate historical votes
    df_historical = aggregate_historical_votes(district_mapping)
    
    # Step 3: Analyze competitive districts
    analyze_competitive_districts(df_historical)
    
    print("\nProcessing complete!")
    print("Generated files:")
    print("- current_district_structure.json: Mapping of current districts to towns")
    print("- historical_elections_in_current_districts_v2.csv: Historical votes aggregated by current districts")
    print("- competitive_districts_historical.csv: Districts with <10% average margin")

if __name__ == "__main__":
    main()