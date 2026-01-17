#!/usr/bin/env python3
"""
Debug the vote mapping to understand the discrepancy
"""

import pandas as pd
import json

# Check a specific district that should be straightforward
# Let's look at Belknap-1 in 2016

# First, what towns are in Belknap-1?
with open('current_district_structure.json', 'r') as f:
    districts = json.load(f)

print("Belknap-1 towns:", districts['Belknap-1'])

# Now check the 2016 data for these towns
df_2016 = pd.read_csv('nh_election_data/2016_parsed_results.csv')
belknap1_towns = districts['Belknap-1']

print("\n2016 votes in Belknap-1 towns:")
for town in belknap1_towns:
    town_data = df_2016[df_2016['town'] == town]
    print(f"\n{town}:")
    print(town_data[['candidate', 'party', 'votes']])

# Check what district these towns were in during 2016
print("\n2016 district assignments:")
for town in belknap1_towns:
    town_data = df_2016[df_2016['town'] == town]
    if not town_data.empty:
        print(f"{town} was in district: {town_data['district'].iloc[0]}")

# Now check who actually won in the historical Belknap 1
with open('nh_election_data/2016_winners.json', 'r') as f:
    winners_2016 = json.load(f)

if 'Belknap 1' in winners_2016:
    print("\n2016 winners in historical Belknap 1:")
    for winner in winners_2016['Belknap 1']['winners']:
        print(f"  {winner.get('candidate', 'Unknown')} ({winner['party']})")

# Check our calculation
calc_df = pd.read_csv('district_seat_allocations_detailed.csv')
our_calc = calc_df[(calc_df['county'] == 'Belknap') & 
                   (calc_df['districtNum'] == 1) & 
                   (calc_df['year'] == 2016)]
print("\nOur calculation for Belknap-1 in 2016:")
print(f"R votes: {our_calc['total_R'].iloc[0]}, D votes: {our_calc['total_D'].iloc[0]}")
print(f"We allocated: {our_calc['R_total_seats'].iloc[0]}R, {our_calc['D_total_seats'].iloc[0]}D")