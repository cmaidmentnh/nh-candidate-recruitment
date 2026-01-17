import pandas as pd
import numpy as np
from pathlib import Path

def analyze_rockingham_districts():
    """Analyze Rockingham county districts 1, 5, and 6"""
    file_path = Path("/Users/chrismaidment/Downloads/candidate_web_app/nh_election_data/2024-ge-house-rockingham_3.xlsx")
    
    print("=" * 80)
    print("ANALYZING ROCKINGHAM COUNTY")
    print("=" * 80)
    
    # Read all sheets to understand structure
    xls = pd.ExcelFile(file_path)
    print(f"\nSheets in file: {xls.sheet_names}")
    
    # Read the main sheet
    df = pd.read_excel(file_path, sheet_name=0)
    
    print(f"\nDataFrame shape: {df.shape}")
    print(f"\nColumn names: {list(df.columns)}")
    
    # Print first few rows to understand structure
    print("\nFirst 10 rows:")
    print(df.head(10).to_string())
    
    # Look for District 1
    print("\n" + "="*60)
    print("DISTRICT 1 ANALYSIS")
    print("="*60)
    
    # Find rows containing District 1
    district_1_mask = df.astype(str).apply(lambda x: x.str.contains('District 1', case=False, na=False)).any(axis=1)
    if district_1_mask.any():
        start_idx = df[district_1_mask].index[0]
        print(f"\nFound District 1 at row {start_idx}")
        
        # Show surrounding rows
        print("\nRows around District 1:")
        for i in range(max(0, start_idx-2), min(len(df), start_idx+20)):
            print(f"Row {i}: {list(df.iloc[i])}")
    
    # Look for District 5
    print("\n" + "="*60)
    print("DISTRICT 5 ANALYSIS")
    print("="*60)
    
    district_5_mask = df.astype(str).apply(lambda x: x.str.contains('District 5', case=False, na=False)).any(axis=1)
    if district_5_mask.any():
        start_idx = df[district_5_mask].index[0]
        print(f"\nFound District 5 at row {start_idx}")
        
        # Show surrounding rows
        print("\nRows around District 5:")
        for i in range(max(0, start_idx-2), min(len(df), start_idx+20)):
            print(f"Row {i}: {list(df.iloc[i])}")
    
    # Look for District 6
    print("\n" + "="*60)
    print("DISTRICT 6 ANALYSIS")
    print("="*60)
    
    district_6_mask = df.astype(str).apply(lambda x: x.str.contains('District 6', case=False, na=False)).any(axis=1)
    if district_6_mask.any():
        start_idx = df[district_6_mask].index[0]
        print(f"\nFound District 6 at row {start_idx}")
        
        # Show surrounding rows
        print("\nRows around District 6:")
        for i in range(max(0, start_idx-2), min(len(df), start_idx+20)):
            print(f"Row {i}: {list(df.iloc[i])}")
    
    # Check if there are any recount-related columns
    print("\n" + "="*60)
    print("CHECKING FOR RECOUNT COLUMNS")
    print("="*60)
    
    recount_cols = [col for col in df.columns if 'recount' in str(col).lower()]
    if recount_cols:
        print(f"\nFound recount columns: {recount_cols}")
    else:
        print("\nNo columns with 'recount' in name")
    
    # Check for BLC columns
    blc_cols = [col for col in df.columns if 'blc' in str(col).lower()]
    if blc_cols:
        print(f"\nFound BLC columns: {blc_cols}")
    else:
        print("\nNo columns with 'BLC' in name")

def analyze_strafford_districts():
    """Analyze Strafford county district 8"""
    file_path = Path("/Users/chrismaidment/Downloads/candidate_web_app/nh_election_data/2024-ge-house-strafford_3.xls")
    
    print("\n" + "=" * 80)
    print("ANALYZING STRAFFORD COUNTY")
    print("=" * 80)
    
    # Read all sheets to understand structure
    xls = pd.ExcelFile(file_path)
    print(f"\nSheets in file: {xls.sheet_names}")
    
    # Read the main sheet
    df = pd.read_excel(file_path, sheet_name=0)
    
    print(f"\nDataFrame shape: {df.shape}")
    print(f"\nColumn names: {list(df.columns)}")
    
    # Print first few rows to understand structure
    print("\nFirst 10 rows:")
    print(df.head(10).to_string())
    
    # Look for District 8
    print("\n" + "="*60)
    print("DISTRICT 8 ANALYSIS")
    print("="*60)
    
    # Find rows containing District 8
    district_8_mask = df.astype(str).apply(lambda x: x.str.contains('District 8', case=False, na=False)).any(axis=1)
    if district_8_mask.any():
        start_idx = df[district_8_mask].index[0]
        print(f"\nFound District 8 at row {start_idx}")
        
        # Show surrounding rows - more rows to capture recount data
        print("\nRows around District 8:")
        for i in range(max(0, start_idx-2), min(len(df), start_idx+25)):
            print(f"Row {i}: {list(df.iloc[i])}")
    
    # Check for recount-related columns
    print("\n" + "="*60)
    print("CHECKING FOR RECOUNT DATA")
    print("="*60)
    
    recount_cols = [col for col in df.columns if 'recount' in str(col).lower()]
    if recount_cols:
        print(f"\nFound recount columns: {recount_cols}")
    
    # Search for recount text in cells
    recount_mask = df.astype(str).apply(lambda x: x.str.contains('recount', case=False, na=False)).any(axis=1)
    if recount_mask.any():
        print("\nRows containing 'recount':")
        for idx in df[recount_mask].index:
            print(f"Row {idx}: {list(df.iloc[idx])}")

if __name__ == "__main__":
    analyze_rockingham_districts()
    analyze_strafford_districts()