#!/usr/bin/env python3
"""
Method 8: Regression analysis to find structural factors
What explains the difference between predictions and actuals?
"""

import pandas as pd
import json
import numpy as np
from sklearn.linear_model import LinearRegression
import matplotlib.pyplot as plt

print("METHOD 8: REGRESSION ANALYSIS OF STRUCTURAL FACTORS")
print("="*80)

# Load all necessary data
current_districts = json.load(open('current_district_structure.json'))

# Build a comprehensive dataset of district characteristics
district_features = []

# Get seat counts
seats_df = pd.read_csv('2022_nh_winners_comprehensive.csv')
seat_counts = {}
for county in seats_df['county'].unique():
    for district in seats_df[seats_df['county'] == county]['district'].unique():
        key = f"{county}-{district}"
        seat_counts[key] = len(seats_df[(seats_df['county'] == county) & (seats_df['district'] == district)])

# Analyze each district across years
for dist_key, towns in current_districts.items():
    seats = seat_counts.get(dist_key, 1)
    
    # Calculate features for each year
    for year in [2016, 2018, 2020]:
        df = pd.read_csv(f'nh_election_data/{year}_parsed_results.csv')
        
        # Get vote data
        dist_r = 0
        dist_d = 0
        r_candidates = set()
        d_candidates = set()
        town_count = 0
        
        for town in towns:
            town_data = df[df['town'] == town]
            if town_data.empty and ' Ward ' in town:
                town_data = df[df['town'] == town.replace(' Ward ', ' Wd ')]
            
            if not town_data.empty:
                town_count += 1
                dist_r += town_data[town_data['party'] == 'R']['votes'].sum()
                dist_d += town_data[town_data['party'] == 'D']['votes'].sum()
                
                # Count unique candidates
                r_candidates.update(town_data[town_data['party'] == 'R']['candidate'].unique())
                d_candidates.update(town_data[town_data['party'] == 'D']['candidate'].unique())
        
        if dist_r + dist_d > 0:
            r_share = dist_r / (dist_r + dist_d)
            
            # Calculate features
            features = {
                'district': dist_key,
                'year': year,
                'seats': seats,
                'r_vote_share': r_share,
                'total_votes': dist_r + dist_d,
                'r_candidates': len(r_candidates),
                'd_candidates': len(d_candidates),
                'towns': len(towns),
                'towns_with_data': town_count,
                'multi_member': 1 if seats > 1 else 0,
                'competitive': 1 if 0.45 <= r_share <= 0.55 else 0,
                'candidate_ratio': len(r_candidates) / len(d_candidates) if len(d_candidates) > 0 else 2,
                'county_' + dist_key.split('-')[0]: 1  # County dummy variables
            }
            
            # Add predicted and actual outcomes
            # Simplified prediction
            if seats == 1:
                pred_r = 1 if r_share > 0.5 else 0
            else:
                if r_share > 0.58:
                    pred_r = min(seats, max(seats // 2 + 1, int(seats * 0.7)))
                elif r_share < 0.42:
                    pred_r = max(0, min(seats // 2 - 1, int(seats * 0.3)))
                else:
                    pred_r = int(seats * r_share + 0.5)
            
            features['predicted_r_seats'] = pred_r
            
            district_features.append(features)

# Convert to DataFrame
df = pd.DataFrame(district_features)

# Add year dummies
df['year_2018'] = (df['year'] == 2018).astype(int)
df['year_2020'] = (df['year'] == 2020).astype(int)

print(f"Built dataset with {len(df)} district-year observations")
print(f"Features: {list(df.columns)}")

# Regression analysis
print("\n\nREGRESSION ANALYSIS")
print("-"*60)

# Select features for regression
feature_cols = ['r_vote_share', 'seats', 'multi_member', 'competitive', 
                'candidate_ratio', 'year_2018', 'year_2020']

# Get county dummies
county_cols = [col for col in df.columns if col.startswith('county_')]
feature_cols.extend(county_cols)

# Remove any missing values
df_clean = df[feature_cols + ['predicted_r_seats']].dropna()

X = df_clean[feature_cols]
y = df_clean['predicted_r_seats']

# Fit regression
model = LinearRegression()
model.fit(X, y)

# Print coefficients
print("\nRegression coefficients (impact on R seats):")
for feat, coef in sorted(zip(feature_cols, model.coef_), key=lambda x: abs(x[1]), reverse=True)[:10]:
    print(f"  {feat}: {coef:+.3f}")

print(f"\nR-squared: {model.score(X, y):.3f}")

# Analyze residuals by district type
print("\n\nRESIDUAL ANALYSIS")
print("-"*60)

df_clean['residual'] = y - model.predict(X)

# By competitiveness
print("\nAverage residuals by competitiveness:")
print(f"  Competitive districts: {df_clean[df_clean['competitive'] == 1]['residual'].mean():+.3f}")
print(f"  Safe districts: {df_clean[df_clean['competitive'] == 0]['residual'].mean():+.3f}")

# By multi-member status
print("\nAverage residuals by district type:")
print(f"  Single-member: {df_clean[df_clean['multi_member'] == 0]['residual'].mean():+.3f}")
print(f"  Multi-member: {df_clean[df_clean['multi_member'] == 1]['residual'].mean():+.3f}")

# Find districts with largest prediction errors
print("\n\nDISTRICTS WITH LARGEST PREDICTION ERRORS")
print("-"*60)

# Aggregate by district
district_errors = df.groupby('district').agg({
    'predicted_r_seats': 'mean',
    'r_vote_share': 'mean',
    'seats': 'first'
}).reset_index()

# Add actual performance from 2022
actual_2022 = {}
winners_2022 = pd.read_csv('2022_nh_winners_comprehensive.csv')
for _, row in winners_2022.iterrows():
    key = f"{row['county']}-{row['district']}"
    if key not in actual_2022:
        actual_2022[key] = {'R': 0, 'D': 0}
    if row['party'] == 'R':
        actual_2022[key]['R'] += 1
    else:
        actual_2022[key]['D'] += 1

district_errors['actual_r_2022'] = district_errors['district'].map(lambda x: actual_2022.get(x, {}).get('R', 0))
district_errors['error'] = district_errors['actual_r_2022'] - district_errors['predicted_r_seats']

# Sort by error magnitude
district_errors['abs_error'] = district_errors['error'].abs()
top_errors = district_errors.nlargest(10, 'abs_error')

print("\nDistricts where actual 2022 results most differed from historical prediction:")
for _, row in top_errors.iterrows():
    print(f"  {row['district']}: Predicted {row['predicted_r_seats']:.1f}R, "
          f"Actual {row['actual_r_2022']}R (error: {row['error']:+.1f})")

# Summary insight
print("\n\nKEY INSIGHTS")
print("="*80)

# Calculate systematic bias
avg_error = district_errors['error'].mean()
print(f"\nAverage prediction error: {avg_error:+.2f} seats per district")
print(f"Total systematic error: {avg_error * len(district_errors):+.1f} seats")

# Identify structural factors
if abs(avg_error) > 0.05:
    if avg_error > 0:
        print("\nCurrent districts appear to FAVOR Republicans more than vote shares suggest")
        print("Possible factors:")
        print("- Multi-member district dynamics favor majority party more than modeled")
        print("- Geographic clustering creates natural Republican advantages")
        print("- Candidate recruitment advantages in certain areas")
    else:
        print("\nCurrent districts appear to DISADVANTAGE Republicans relative to vote shares")
        print("Possible factors:")
        print("- Democratic voters more efficiently distributed")
        print("- Republican voters more concentrated (packed)")
        print("- Competitive districts lean slightly Democratic")