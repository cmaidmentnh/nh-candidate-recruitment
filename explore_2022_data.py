#!/usr/bin/env python3
"""
Explore 2022 NH election data to understand the file structure
"""

import pandas as pd

# Read one file to understand the structure
df = pd.read_excel('nh_election_data/2022-ge-house-belknap_1.xls')

print("BELKNAP COUNTY 2022 DATA")
print("="*50)
print(f"Shape: {df.shape}")
print(f"Columns: {df.columns.tolist()}")
print("\nFirst 20 rows:")
print(df.head(20).to_string())