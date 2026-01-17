#!/usr/bin/env python3
"""
Fix the predictive modeling to properly account for political environment shifts
"""

import pandas as pd
import numpy as np

def calculate_environment_adjusted_predictions():
    """Calculate predictions that properly respond to political environments"""
    
    # Load the PVI data
    pvi_df = pd.read_csv('nh_house_pvi_final.csv')
    
    # Define scenarios
    scenarios = {
        'neutral': 0,
        'd5': -5,
        'r5': 5
    }
    
    predictions = {}
    
    for scenario, shift in scenarios.items():
        # Calculate probability of R win based on adjusted PVI
        # Using a logistic function that's more sensitive to environment
        adjusted_pvi = pvi_df['pvi'] + shift
        
        # Convert adjusted PVI to probability
        # At PVI = 0, probability = 0.5
        # Each point of PVI changes probability by ~2.5%
        prob_r = 1 / (1 + np.exp(-adjusted_pvi * 0.1))
        
        # For very safe seats, use a threshold approach
        # If adjusted PVI > 10, very likely R
        # If adjusted PVI < -10, very likely D
        prob_r = np.where(adjusted_pvi > 10, 0.95 + (adjusted_pvi - 10) * 0.002, prob_r)
        prob_r = np.where(adjusted_pvi < -10, 0.05 - (adjusted_pvi + 10) * 0.002, prob_r)
        
        # Ensure probabilities stay in [0, 1]
        prob_r = np.clip(prob_r, 0.01, 0.99)
        
        # Calculate expected seats
        # For multi-member districts, multiply probability by seats
        expected_r_seats = (prob_r * pvi_df['seats']).sum()
        expected_d_seats = ((1 - prob_r) * pvi_df['seats']).sum()
        
        # Round to get actual seat predictions
        # Use probabilistic rounding for more realistic results
        district_r_seats = []
        district_d_seats = []
        
        for idx, row in pvi_df.iterrows():
            seats = row['seats']
            p_r = prob_r[idx]
            
            if seats == 1:
                # Single member district - winner take all
                if p_r > 0.5:
                    district_r_seats.append(1)
                    district_d_seats.append(0)
                else:
                    district_r_seats.append(0)
                    district_d_seats.append(1)
            else:
                # Multi-member district - proportional
                r_seats = int(round(seats * p_r))
                # Ensure we don't exceed total seats
                r_seats = min(r_seats, seats)
                d_seats = seats - r_seats
                district_r_seats.append(r_seats)
                district_d_seats.append(d_seats)
        
        total_r = sum(district_r_seats)
        total_d = sum(district_d_seats)
        
        predictions[scenario] = {
            'R_seats': total_r,
            'D_seats': total_d,
            'total': total_r + total_d
        }
        
        # Save detailed predictions
        pvi_df[f'prob_r_{scenario}'] = prob_r
        pvi_df[f'pred_r_seats_{scenario}'] = district_r_seats
        pvi_df[f'pred_d_seats_{scenario}'] = district_d_seats
    
    # Print results
    print("\nCorrected Predictions by Scenario:")
    print("| Scenario | R Seats | D Seats | Total |")
    print("|----------|---------|---------|-------|")
    for scenario, pred in predictions.items():
        print(f"| {scenario.upper():8s} | {pred['R_seats']:7d} | {pred['D_seats']:7d} | {pred['total']:5d} |")
    
    # Identify swing districts
    print("\nSwing Districts (probability changes >20% between scenarios):")
    swing_threshold = 0.2
    
    pvi_df['prob_swing'] = pvi_df['prob_r_r5'] - pvi_df['prob_r_d5']
    swing_districts = pvi_df[pvi_df['prob_swing'] > swing_threshold].sort_values('prob_swing', ascending=False)
    
    print(f"\nFound {len(swing_districts)} highly responsive districts:")
    for _, dist in swing_districts.head(10).iterrows():
        print(f"  {dist['county']}-{dist['district']}: PVI {dist['pvi_label']}, "
              f"D+5: {dist['prob_r_d5']:.1%} R, R+5: {dist['prob_r_r5']:.1%} R")
    
    # Save corrected predictions
    output_cols = ['county', 'district', 'pvi', 'pvi_label', 'seats', 
                   'prob_r_neutral', 'prob_r_d5', 'prob_r_r5',
                   'pred_r_seats_neutral', 'pred_d_seats_neutral',
                   'pred_r_seats_d5', 'pred_d_seats_d5',
                   'pred_r_seats_r5', 'pred_d_seats_r5']
    
    pvi_df[output_cols].to_csv('nh_house_analysis_outputs/predictive_model_results_corrected.csv', index=False)
    
    return predictions

if __name__ == "__main__":
    predictions = calculate_environment_adjusted_predictions()