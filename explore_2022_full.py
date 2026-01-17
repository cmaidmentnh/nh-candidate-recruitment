#!/usr/bin/env python3
"""
Explore full 2022 NH election data structure
"""

import pandas as pd

# Read Belknap file to understand the structure
df = pd.read_excel('nh_election_data/2022-ge-house-belknap_1.xls')

print("BELKNAP COUNTY 2022 DATA - FULL VIEW")
print("="*60)
print(f"Shape: {df.shape}")

print("\nALL ROWS:")
for i in range(len(df)):
    row_data = []
    for col in range(len(df.columns)):
        val = df.iloc[i, col]
        if pd.notna(val) and str(val).strip():
            row_data.append(f"[{col}]{val}")
    if row_data:
        print(f"Row {i:2d}: {' | '.join(row_data)}")
    else:
        print(f"Row {i:2d}: [empty]")