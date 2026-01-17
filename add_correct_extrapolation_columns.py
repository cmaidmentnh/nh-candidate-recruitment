#!/usr/bin/env python3
"""
Add correct extrapolation columns with proper logic for unopposed/partially opposed races
"""

import pandas as pd
import numpy as np

# Load the data
df = pd.read_csv('comprehensive_district_town_data.csv')

# Group by district and year to get district-level data
district_df = df.groupby(['county', 'districtNum', 'seats', 'year']).agg({
    'total_R': 'sum',
    'total_D': 'sum',
    'total_Other': 'sum',
    'R_candidate_count': 'max',  # Max because we want distinct candidates per district
    'D_candidate_count': 'max',
    'R_avg_votes': 'mean',  # Average of town averages
    'D_avg_votes': 'mean'
}).reset_index()

# Column M & N: Calculate defaults more carefully
# R_defaults = unopposed R candidates (where R has candidates but D doesn't have enough)
# D_defaults = unopposed D candidates (where D has candidates but R doesn't have enough)
def calculate_defaults(row):
    r_count = row['R_candidate_count']
    d_count = row['D_candidate_count']
    seats = row['seats']
    
    # If either party has 0 candidates, the other party gets all their candidates as defaults
    if d_count == 0 and r_count > 0:
        r_defaults = min(r_count, seats)
        d_defaults = 0
    elif r_count == 0 and d_count > 0:
        r_defaults = 0
        d_defaults = min(d_count, seats)
    elif r_count + d_count <= seats:
        # Both parties combined have fewer candidates than seats
        # Each gets all their candidates
        r_defaults = r_count
        d_defaults = d_count
    else:
        # Normal case - some competition
        # Defaults are candidates beyond what the other party can contest
        r_defaults = max(0, r_count - d_count) if r_count > d_count else 0
        d_defaults = max(0, d_count - r_count) if d_count > r_count else 0
        
        # But defaults can't exceed seats minus the minimum candidates that will compete
        min_competing = min(r_count, d_count)
        r_defaults = min(r_defaults, max(0, seats - min_competing))
        d_defaults = min(d_defaults, max(0, seats - min_competing))
    
    return r_defaults, d_defaults

district_df['R_defaults'], district_df['D_defaults'] = zip(*district_df.apply(calculate_defaults, axis=1))

# Column O: R_competitive_votes = IF(R_candidates <= seats, MIN(R_candidates, D_candidates) * R_avg_votes, 0)
district_df['R_competitive_votes'] = district_df.apply(
    lambda row: min(row['R_candidate_count'], row['D_candidate_count']) * row['R_avg_votes'] 
    if row['R_candidate_count'] <= row['seats'] else 0,
    axis=1
)

# Column P: D_competitive_votes = IF(D_candidates <= seats, MIN(R_candidates, D_candidates) * D_avg_votes, 0)
district_df['D_competitive_votes'] = district_df.apply(
    lambda row: min(row['R_candidate_count'], row['D_candidate_count']) * row['D_avg_votes']
    if row['D_candidate_count'] <= row['seats'] else 0,
    axis=1
)

# Column Q: Competitive_seats = seats - MAX(D_defaults, R_defaults)
# Special case: if no candidates at all, all seats are unallocated
district_df['competitive_seats'] = district_df.apply(
    lambda row: 0 if (row['R_candidate_count'] == 0 and row['D_candidate_count'] == 0) 
    else row['seats'] - max(row['D_defaults'], row['R_defaults']),
    axis=1
)

# Column R: Allocate competitive seats based on vote totals
def allocate_competitive_seats(row):
    competitive_seats = row['competitive_seats']
    r_votes = row['R_competitive_votes']
    d_votes = row['D_competitive_votes']
    
    if competitive_seats == 0:
        return 0, 0
    
    if r_votes + d_votes == 0:
        # No competitive votes, split evenly
        r_comp = competitive_seats // 2
        d_comp = competitive_seats - r_comp
        return r_comp, d_comp
    
    r_share = r_votes / (r_votes + d_votes)
    
    if competitive_seats == 1:
        # Winner take all for single competitive seat
        if r_share > 0.5:
            return 1, 0
        else:
            return 0, 1
    else:
        # Multiple competitive seats - proportional with majority bonus
        if r_share > 0.65:
            # Strong R advantage
            r_comp = competitive_seats
            d_comp = 0
        elif r_share > 0.55:
            # R advantage with bonus
            r_comp = max(int(competitive_seats * 0.6), int(competitive_seats * r_share + 0.5))
            r_comp = min(r_comp, competitive_seats)
            d_comp = competitive_seats - r_comp
        elif r_share > 0.45:
            # Competitive range
            if r_share > 0.5:
                r_comp = max(int(competitive_seats * r_share + 0.5), (competitive_seats + 1) // 2)
                r_comp = min(r_comp, competitive_seats)
                d_comp = competitive_seats - r_comp
            else:
                d_comp = max(int(competitive_seats * (1 - r_share) + 0.5), (competitive_seats + 1) // 2)
                d_comp = min(d_comp, competitive_seats)
                r_comp = competitive_seats - d_comp
        elif r_share > 0.35:
            # D advantage with bonus
            d_share = 1 - r_share
            d_comp = max(int(competitive_seats * 0.6), int(competitive_seats * d_share + 0.5))
            d_comp = min(d_comp, competitive_seats)
            r_comp = competitive_seats - d_comp
        else:
            # Strong D advantage
            d_comp = competitive_seats
            r_comp = 0
            
    return r_comp, d_comp

# Apply competitive seat allocation
district_df['R_competitive_wins'], district_df['D_competitive_wins'] = zip(*district_df.apply(allocate_competitive_seats, axis=1))

# Calculate total seats won
# Special handling for districts with no candidates - seats go unallocated
district_df['R_total_seats'] = district_df.apply(
    lambda row: 0 if (row['R_candidate_count'] == 0 and row['D_candidate_count'] == 0)
    else row['R_defaults'] + row['R_competitive_wins'],
    axis=1
)
district_df['D_total_seats'] = district_df.apply(
    lambda row: 0 if (row['R_candidate_count'] == 0 and row['D_candidate_count'] == 0)
    else row['D_defaults'] + row['D_competitive_wins'],
    axis=1
)

# Verify totals
district_df['total_allocated'] = district_df['R_total_seats'] + district_df['D_total_seats']
district_df['unallocated_seats'] = district_df['seats'] - district_df['total_allocated']
district_df['allocation_check'] = (district_df['total_allocated'] == district_df['seats']) | (
    (district_df['R_candidate_count'] == 0) & (district_df['D_candidate_count'] == 0)
)

# Save the detailed results
district_df.to_csv('district_seat_allocations_detailed.csv', index=False)

# Create summary by year
year_summary = district_df.groupby('year').agg({
    'R_defaults': 'sum',
    'D_defaults': 'sum',
    'R_competitive_wins': 'sum',
    'D_competitive_wins': 'sum',
    'R_total_seats': 'sum',
    'D_total_seats': 'sum',
    'unallocated_seats': 'sum',
    'seats': 'sum',
    'allocation_check': 'all'  # Should be True for all
}).reset_index()

# Add actual results for comparison
actual_results = {
    2016: {'R': 226, 'D': 174},
    2018: {'R': 167, 'D': 233},
    2020: {'R': 213, 'D': 187},
    2022: {'R': 201, 'D': 198},
    2024: {'R': 222, 'D': 178}
}

year_summary['actual_R'] = year_summary['year'].map(lambda y: actual_results[y]['R'])
year_summary['actual_D'] = year_summary['year'].map(lambda y: actual_results[y]['D'])
year_summary['R_difference'] = year_summary['R_total_seats'] - year_summary['actual_R']
year_summary['D_difference'] = year_summary['D_total_seats'] - year_summary['actual_D']

print("\nSEAT ALLOCATION SUMMARY BY YEAR")
print("="*80)
print(f"{'Year':>6} {'R_def':>6} {'D_def':>6} {'R_comp':>6} {'D_comp':>6} {'R_tot':>6} {'D_tot':>6} {'Unall':>6} {'Act_R':>6} {'Act_D':>6} {'R_diff':>6} {'D_diff':>6}")
print("-"*80)
for _, row in year_summary.iterrows():
    print(f"{row['year']:>6} {row['R_defaults']:>6} {row['D_defaults']:>6} "
          f"{row['R_competitive_wins']:>6} {row['D_competitive_wins']:>6} "
          f"{row['R_total_seats']:>6} {row['D_total_seats']:>6} "
          f"{row['unallocated_seats']:>6} "
          f"{row['actual_R']:>6} {row['actual_D']:>6} "
          f"{row['R_difference']:>+6} {row['D_difference']:>+6}")

# Save summary
year_summary.to_csv('seat_allocation_summary_detailed.csv', index=False)

# Also merge back to original dataframe with all columns
df_merged = df.merge(
    district_df[['county', 'districtNum', 'year', 'R_defaults', 'D_defaults', 
                 'R_competitive_votes', 'D_competitive_votes', 'competitive_seats',
                 'R_competitive_wins', 'D_competitive_wins', 'R_total_seats', 'D_total_seats']],
    on=['county', 'districtNum', 'year'],
    how='left'
)

# Save the comprehensive file with all columns
df_merged.to_csv('comprehensive_district_town_data_final.csv', index=False)

print(f"\n\nFiles created:")
print(f"- comprehensive_district_town_data_final.csv (original data with new columns)")
print(f"- district_seat_allocations_detailed.csv (district-level calculations)")
print(f"- seat_allocation_summary_detailed.csv (year summaries)")

# Check allocation accuracy
if not year_summary['allocation_check'].all():
    print("\nWARNING: Some districts have allocation errors!")
else:
    print("\nAll seat allocations verified correctly.")