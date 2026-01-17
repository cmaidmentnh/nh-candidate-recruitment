#!/usr/bin/env python3
"""
Parse all NH House elections (2016-2024) to get comprehensive vote data
"""

import pandas as pd
import glob
import re
import csv
import subprocess
import os

def parse_year(year):
    """Parse a specific year's election data"""
    print(f"\nParsing {year} election data...")
    
    # Check if we already have the comprehensive results
    if os.path.exists(f'{year}_nh_all_results_comprehensive.csv'):
        print(f"  Found existing comprehensive results for {year}")
        return True
    
    # Create a temporary parser for this year
    with open('comprehensive_town_parser.py', 'r') as f:
        parser_content = f.read()
    
    # Modify for the specific year
    parser_content = parser_content.replace('2022', str(year))
    parser_content = parser_content.replace("glob.glob('nh_election_data/*2022*.xls*')", 
                                          f"glob.glob('nh_election_data/*{year}*.xls*')")
    parser_content = parser_content.replace("'year': 2022,", f"'year': {year},")
    parser_content = parser_content.replace("2022_nh_all_results_comprehensive.csv", 
                                          f"{year}_nh_all_results_comprehensive.csv")
    parser_content = parser_content.replace("2022_nh_winners_comprehensive.csv", 
                                          f"{year}_nh_winners_comprehensive.csv")
    
    # Write temporary parser
    temp_parser = f'parse_{year}_temp.py'
    with open(temp_parser, 'w') as f:
        f.write(parser_content)
    
    # Run the parser
    try:
        result = subprocess.run(['python3', temp_parser], 
                              capture_output=True, text=True, timeout=300)
        print(f"  Parser output: {result.stdout.split('Wrote')[-1].strip() if 'Wrote' in result.stdout else 'Processing...'}")
        if result.stderr:
            print(f"  Errors: {result.stderr[:200]}")
        
        # Clean up
        os.remove(temp_parser)
        
        # Check if files were created
        if os.path.exists(f'{year}_nh_all_results_comprehensive.csv'):
            df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
            print(f"  Successfully parsed {len(df)} vote records for {year}")
            return True
        else:
            print(f"  Failed to create results file for {year}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"  Parser timed out for {year}")
        os.remove(temp_parser)
        return False
    except Exception as e:
        print(f"  Error parsing {year}: {e}")
        if os.path.exists(temp_parser):
            os.remove(temp_parser)
        return False

def main():
    """Parse all years of election data"""
    years = [2016, 2018, 2020, 2022, 2024]
    successful_years = []
    
    for year in years:
        if parse_year(year):
            successful_years.append(year)
    
    print(f"\n{'='*60}")
    print(f"Successfully parsed {len(successful_years)} out of {len(years)} elections:")
    print(f"Years with data: {successful_years}")
    
    # Verify what we have
    print(f"\n{'='*60}")
    print("Verification of parsed data:")
    for year in successful_years:
        try:
            vote_df = pd.read_csv(f'{year}_nh_all_results_comprehensive.csv')
            winners_df = pd.read_csv(f'{year}_nh_winners_comprehensive.csv')
            print(f"{year}: {len(vote_df)} vote records, {len(winners_df)} winners")
        except:
            print(f"{year}: Error reading files")

if __name__ == "__main__":
    main()