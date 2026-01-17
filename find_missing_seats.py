#!/usr/bin/env python3
"""
Find why we're missing 8 seats - compare expected vs actual winners
"""

import pandas as pd

# Read the data
districts_df = pd.read_csv('nh_election_data/2022_all_districts.csv')
winners_df = pd.read_csv('nh_election_data/2022_winners_fixed.csv')

# Count winners per district
winners_per_district = winners_df.groupby('District').size().reset_index(columns=['Winners'])

# Merge with expected seats
comparison = districts_df.merge(winners_per_district, on='District', how='left')
comparison['Winners'] = comparison['Winners'].fillna(0).astype(int)
comparison['Missing'] = comparison['Seats'] - comparison['Winners']

# Find districts with missing seats
missing_seats = comparison[comparison['Missing'] > 0]

print("DISTRICTS WITH MISSING SEATS:")
print("=" * 50)
for _, row in missing_seats.iterrows():
    print(f"{row['District']}: Expected {row['Seats']}, Got {row['Winners']}, Missing {row['Missing']}")

print(f"\nTotal missing seats: {missing_seats['Missing'].sum()}")

# Check if any districts have zero candidates in vote data
votes_df = pd.read_csv('nh_election_data/2022_all_votes_fixed.csv')
districts_in_votes = set(votes_df['District'].unique())
all_districts = set(districts_df['District'])

districts_no_votes = all_districts - districts_in_votes
if districts_no_votes:
    print(f"\nDistricts with NO vote data: {len(districts_no_votes)}")
    for dist in sorted(districts_no_votes):
        seats = districts_df[districts_df['District'] == dist]['Seats'].iloc[0]
        print(f"  {dist} ({seats} seats)")

# Look at specific problematic districts
print(f"\nDetailed analysis of missing seat districts:")
for _, row in missing_seats.iterrows():
    dist = row['District']
    print(f"\n{dist}:")
    dist_votes = votes_df[votes_df['District'] == dist]
    if len(dist_votes) == 0:
        print("  NO VOTE DATA FOUND")
    else:
        candidates = dist_votes.groupby(['Candidate', 'Party'])['Votes'].sum().reset_index()
        candidates = candidates.sort_values('Votes', ascending=False)
        print(f"  Candidates found: {len(candidates)}")
        for _, cand in candidates.head(10).iterrows():
            print(f"    {cand['Candidate']} ({cand['Party']}): {cand['Votes']}")