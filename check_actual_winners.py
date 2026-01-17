#!/usr/bin/env python3
"""
Check actual winners from historical data
"""

import json
import pandas as pd

# Load actual winners for each year
for year in [2016, 2018, 2020]:
    with open(f'nh_election_data/{year}_winners.json', 'r') as f:
        winners = json.load(f)
    
    total_r = 0
    total_d = 0
    total_other = 0
    
    for district, info in winners.items():
        for winner in info['winners']:
            if winner['party'] == 'R':
                total_r += 1
            elif winner['party'] == 'D':
                total_d += 1
            else:
                total_other += 1
    
    print(f"\n{year} ACTUAL WINNERS:")
    print(f"R: {total_r}, D: {total_d}, Other: {total_other}, Total: {total_r + total_d + total_other}")

# Now check what we calculated
df = pd.read_csv('seat_allocation_summary_detailed.csv')
print("\nOUR CALCULATIONS:")
print(df[['year', 'R_total_seats', 'D_total_seats', 'actual_R', 'actual_D']])