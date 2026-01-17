import pandas as pd
import numpy as np
from pathlib import Path

def analyze_rockingham_districts():
    """Analyze specific Rockingham districts: 1, 5, and 6"""
    file_path = Path("/Users/chrismaidment/Downloads/candidate_web_app/nh_election_data/2024-ge-house-rockingham_3.xlsx")
    
    print("=" * 100)
    print("ROCKINGHAM COUNTY - DISTRICTS 1, 5, and 6")
    print("=" * 100)
    
    df = pd.read_excel(file_path, sheet_name=0)
    
    # District 1 Analysis
    print("\n" + "="*80)
    print("DISTRICT 1 (3 seats)")
    print("="*80)
    
    # Based on the output, District 1 is at row 2
    print("\nCandidate Row (Row 2):")
    print("Democrats:")
    print(f"  - Charlotte Fyfe, d: Col 1")
    print(f"  - Hal Rafter, d: Col 2")
    print(f"  - Pamela Sanderson, d: Col 3")
    print("Republicans:")
    print(f"  - Scott R. Bryer, r: Col 4")
    print(f"  - James Guzofski, r: Col 5")
    print(f"  - Paul D. Tudor, r: Col 6")
    
    print("\nVote Totals (Row 5):")
    totals_row = df.iloc[5]
    print(f"Charlotte Fyfe (D): {totals_row[1]}")
    print(f"Hal Rafter (D): {totals_row[2]}")
    print(f"Pamela Sanderson (D): {totals_row[3]}")
    print(f"Scott R. Bryer (R): {totals_row[4]}")
    print(f"James Guzofski (R): {totals_row[5]}")
    print(f"Paul D. Tudor (R): {totals_row[6]}")
    
    # District 5 Analysis
    print("\n" + "="*80)
    print("DISTRICT 5 (2 seats)")
    print("="*80)
    
    # District 5 is at row 15
    print("\nCandidate Row (Row 15):")
    candidates_row = df.iloc[15]
    print("Democrats:")
    print(f"  - {candidates_row[1]}: Col 1")
    print(f"  - {candidates_row[2]}: Col 2")
    print("Republicans:")
    print(f"  - {candidates_row[3]}: Col 3")
    print(f"  - {candidates_row[4]}: Col 4")
    
    print("\nVote Totals (Row 16 - Epping only, no totals row):")
    vote_row = df.iloc[16]
    print(f"{candidates_row[1]}: {vote_row[1]}")
    print(f"{candidates_row[2]}: {vote_row[2]}")
    print(f"{candidates_row[3]}: {vote_row[3]}")
    print(f"{candidates_row[4]}: {vote_row[4]}")
    
    # Check for recount data around District 5
    print("\nChecking for recount data around District 5...")
    for i in range(14, min(20, len(df))):
        row_str = ' '.join([str(v) for v in df.iloc[i] if pd.notna(v)])
        if 'recount' in row_str.lower():
            print(f"Found recount at row {i}: {row_str}")
    
    # District 6 Analysis
    print("\n" + "="*80)
    print("DISTRICT 6 (1 seat)")
    print("="*80)
    
    # District 6 is at row 18
    print("\nCandidate Row (Row 18):")
    candidates_row = df.iloc[18]
    print(f"Democrat: {candidates_row[1]}")
    print(f"Republican: {candidates_row[2]}")
    
    print("\nVote Totals (Row 19 - Brentwood only):")
    vote_row = df.iloc[19]
    print(f"{candidates_row[1]}: {vote_row[1]}")
    print(f"{candidates_row[2]}: {vote_row[2]}")
    
    # Check for BLC columns
    print("\nChecking for BLC data...")
    for i in range(17, min(22, len(df))):
        row = df.iloc[i]
        for j, val in enumerate(row):
            if pd.notna(val) and 'blc' in str(val).lower():
                print(f"Found BLC at row {i}, col {j}: {val}")

def analyze_strafford_district_8():
    """Analyze Strafford District 8 with recount"""
    file_path = Path("/Users/chrismaidment/Downloads/candidate_web_app/nh_election_data/2024-ge-house-strafford_3.xls")
    
    print("\n" + "=" * 100)
    print("STRAFFORD COUNTY - DISTRICT 8")
    print("=" * 100)
    
    df = pd.read_excel(file_path, sheet_name=0)
    
    # First, let's find District 8
    print("\nSearching for District 8...")
    
    for i in range(len(df)):
        row_str = ' '.join([str(v) for v in df.iloc[i] if pd.notna(v)])
        if 'district' in row_str.lower() and '8' in row_str:
            print(f"\nFound potential District 8 at row {i}:")
            print(f"Content: {row_str}")
            
            # Show context
            print("\nShowing rows around this location:")
            for j in range(max(0, i-2), min(len(df), i+20)):
                row = df.iloc[j]
                non_empty = [(col, val) for col, val in enumerate(row) if pd.notna(val) and str(val).strip()]
                if non_empty:
                    print(f"\nRow {j}:")
                    for col, val in non_empty:
                        print(f"  Col {col}: {val}")
    
    # Look specifically for recount information
    print("\n" + "="*60)
    print("SEARCHING FOR RECOUNT DATA")
    print("="*60)
    
    for i in range(len(df)):
        row = df.iloc[i]
        for j, val in enumerate(row):
            if pd.notna(val) and 'recount' in str(val).lower():
                print(f"\nFound recount at row {i}, col {j}: {val}")
                
                # Show extensive context
                print("\nContext (10 rows before and after):")
                for k in range(max(0, i-10), min(len(df), i+10)):
                    row_k = df.iloc[k]
                    non_empty = [(col, v) for col, v in enumerate(row_k) if pd.notna(v) and str(v).strip()]
                    if non_empty:
                        print(f"\nRow {k}:")
                        for col, v in non_empty:
                            print(f"  Col {col}: {v}")
                break

if __name__ == "__main__":
    analyze_rockingham_districts()
    analyze_strafford_district_8()