#!/usr/bin/env python3
"""
Analyze where the discrepancy comes from
"""

import pandas as pd
import json

# Load our calculations
calc_df = pd.read_csv('district_seat_allocations_detailed.csv')

# Focus on 2016 to understand the -65 seat difference
calc_2016 = calc_df[calc_df['year'] == 2016]

# Find districts where we gave seats to D but historically R won
print("Districts where our calculation differs from likely historical outcome:")
print("(Looking for districts with competitive races)\n")

# Sort by competitive votes to find close races
calc_2016_sorted = calc_2016.sort_values('competitive_seats', ascending=False)

print("Top 10 districts by competitive seats in 2016:")
print(calc_2016_sorted[['county', 'districtNum', 'seats', 'R_candidate_count', 'D_candidate_count', 
                       'total_R', 'total_D', 'R_total_seats', 'D_total_seats']].head(10))

# Check if the issue is that we're mapping towns to wrong districts
# Let's verify the total votes
total_r_votes = calc_2016['total_R'].sum()
total_d_votes = calc_2016['total_D'].sum()

print(f"\nTotal votes in our mapping for 2016:")
print(f"R: {total_r_votes:,}")
print(f"D: {total_d_votes:,}")

# Check original 2016 data
df_2016 = pd.read_csv('nh_election_data/2016_parsed_results.csv')
orig_r = df_2016[df_2016['party'] == 'R']['votes'].sum()
orig_d = df_2016[df_2016['party'] == 'D']['votes'].sum()

print(f"\nTotal votes in original 2016 data:")
print(f"R: {orig_r:,}")
print(f"D: {orig_d:,}")

# The issue might be that historical districts had different boundaries
# Let's check how many districts we're calculating for
print(f"\nNumber of districts in our calculation: {len(calc_2016)}")
print(f"Number with R defaults: {len(calc_2016[calc_2016['R_defaults'] > 0])}")
print(f"Number with D defaults: {len(calc_2016[calc_2016['D_defaults'] > 0])}")
print(f"Number with both candidates having fewer than seats: {len(calc_2016[(calc_2016['R_candidate_count'] > 0) & (calc_2016['D_candidate_count'] > 0) & (calc_2016['R_candidate_count'] + calc_2016['D_candidate_count'] <= calc_2016['seats'])])}")