import pandas as pd
import numpy as np
from pathlib import Path
import re

def find_district_blocks(df, district_num):
    """Find all rows related to a specific district number"""
    patterns = [
        f"District.*{district_num}",
        f"District No\\.? {district_num}",
        f"Dist\\.? {district_num}",
        f"\\b{district_num}\\b.*District",
    ]
    
    for pattern in patterns:
        mask = df.astype(str).apply(lambda x: x.str.contains(pattern, case=False, na=False, regex=True)).any(axis=1)
        if mask.any():
            return df[mask].index.tolist()
    return []

def analyze_rockingham_detailed():
    """Detailed analysis of Rockingham county"""
    file_path = Path("/Users/chrismaidment/Downloads/candidate_web_app/nh_election_data/2024-ge-house-rockingham_3.xlsx")
    
    print("=" * 100)
    print("ROCKINGHAM COUNTY - DETAILED ANALYSIS")
    print("=" * 100)
    
    df = pd.read_excel(file_path, sheet_name=0)
    
    # Search for Districts 1, 5, and 6
    for district_num in [1, 5, 6]:
        print(f"\n{'='*80}")
        print(f"SEARCHING FOR DISTRICT {district_num}")
        print('='*80)
        
        # Find district header rows
        district_indices = find_district_blocks(df, district_num)
        
        if district_indices:
            for idx in district_indices:
                print(f"\nFound potential District {district_num} reference at row {idx}:")
                
                # Show context - 2 rows before and up to 15 rows after
                start = max(0, idx - 2)
                end = min(len(df), idx + 15)
                
                print("\nDetailed row-by-row data:")
                for i in range(start, end):
                    row_data = df.iloc[i].tolist()
                    print(f"\nRow {i}:")
                    for j, val in enumerate(row_data):
                        if pd.notna(val) and str(val).strip():
                            print(f"  Col {j}: {val}")
                
                # Look for vote totals in this section
                print("\nLooking for numeric vote data in this section:")
                for i in range(idx, min(len(df), idx + 15)):
                    row = df.iloc[i]
                    numeric_vals = []
                    for j, val in enumerate(row):
                        if pd.notna(val):
                            try:
                                if isinstance(val, (int, float)) and val > 0:
                                    numeric_vals.append((j, val))
                            except:
                                pass
                    if numeric_vals:
                        print(f"Row {i}: {numeric_vals}")
        else:
            print(f"District {district_num} not found with standard patterns")
    
    # Check for special columns (BLC, Recount)
    print("\n" + "="*80)
    print("CHECKING ALL UNIQUE VALUES IN HEADER ROWS")
    print("="*80)
    
    # Check first 5 rows for any special headers
    for i in range(min(5, len(df))):
        unique_vals = [v for v in df.iloc[i].tolist() if pd.notna(v) and str(v).strip()]
        if unique_vals:
            print(f"\nRow {i} unique values: {unique_vals}")

def analyze_strafford_detailed():
    """Detailed analysis of Strafford county"""
    file_path = Path("/Users/chrismaidment/Downloads/candidate_web_app/nh_election_data/2024-ge-house-strafford_3.xls")
    
    print("\n" + "=" * 100)
    print("STRAFFORD COUNTY - DETAILED ANALYSIS")
    print("=" * 100)
    
    df = pd.read_excel(file_path, sheet_name=0)
    
    # Search for District 8
    print(f"\n{'='*80}")
    print("SEARCHING FOR DISTRICT 8")
    print('='*80)
    
    district_indices = find_district_blocks(df, 8)
    
    if district_indices:
        for idx in district_indices:
            print(f"\nFound potential District 8 reference at row {idx}:")
            
            # Show extensive context for recounts
            start = max(0, idx - 2)
            end = min(len(df), idx + 30)  # More rows to catch recount data
            
            print("\nDetailed row-by-row data:")
            for i in range(start, end):
                row_data = df.iloc[i].tolist()
                print(f"\nRow {i}:")
                for j, val in enumerate(row_data):
                    if pd.notna(val) and str(val).strip():
                        print(f"  Col {j}: {val}")
            
            # Look for vote totals
            print("\nLooking for numeric vote data:")
            for i in range(idx, min(len(df), idx + 30)):
                row = df.iloc[i]
                numeric_vals = []
                for j, val in enumerate(row):
                    if pd.notna(val):
                        try:
                            if isinstance(val, (int, float)) and val > 0:
                                numeric_vals.append((j, val))
                        except:
                            pass
                if numeric_vals:
                    print(f"Row {i}: {numeric_vals}")
    else:
        print("District 8 not found with standard patterns")
        
        # Try alternative search
        print("\nSearching for '8' in all cells...")
        for i in range(len(df)):
            row = df.iloc[i]
            for j, val in enumerate(row):
                if pd.notna(val) and '8' in str(val) and 'district' in str(val).lower():
                    print(f"\nFound at Row {i}, Col {j}: {val}")
                    # Show context
                    for k in range(max(0, i-2), min(len(df), i+20)):
                        print(f"Row {k}: {[v for v in df.iloc[k] if pd.notna(v)]}")
                    break
    
    # Check for recount information
    print("\n" + "="*80)
    print("SEARCHING FOR RECOUNT INFORMATION")
    print("="*80)
    
    # Look for any mention of recount
    for i in range(len(df)):
        row_str = ' '.join([str(v) for v in df.iloc[i] if pd.notna(v)])
        if 'recount' in row_str.lower():
            print(f"\nFound 'recount' at row {i}:")
            for j, val in enumerate(df.iloc[i]):
                if pd.notna(val):
                    print(f"  Col {j}: {val}")
            
            # Show surrounding context
            print("\nContext (5 rows before and after):")
            for k in range(max(0, i-5), min(len(df), i+6)):
                if k != i:
                    non_empty = [(j, v) for j, v in enumerate(df.iloc[k]) if pd.notna(v) and str(v).strip()]
                    if non_empty:
                        print(f"Row {k}: {non_empty}")

if __name__ == "__main__":
    analyze_rockingham_detailed()
    analyze_strafford_detailed()