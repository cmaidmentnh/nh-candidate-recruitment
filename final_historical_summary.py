#!/usr/bin/env python3
"""
Create final summary of historical elections mapped to current districts
Focus on what we can accurately determine
"""

import pandas as pd
import numpy as np

def analyze_actual_results():
    """Analyze the actual election results we have"""
    
    print("ACTUAL NH HOUSE ELECTION RESULTS")
    print("="*60)
    
    # Load actual winner counts
    results = {
        2016: {'R': 226, 'D': 174, 'Other': 0, 'Total': 400},
        2018: {'R': 167, 'D': 233, 'Other': 0, 'Total': 400},
        2020: {'R': 213, 'D': 187, 'Other': 0, 'Total': 400},
        2022: {'R': 201, 'D': 198, 'Other': 1, 'Total': 400},  # 1 vacancy
        2024: {'R': 222, 'D': 178, 'Other': 0, 'Total': 400}
    }
    
    # Calculate baselines from the PVI analysis
    baselines = {
        2016: None,  # No comprehensive data
        2018: None,  # No comprehensive data  
        2020: None,  # No comprehensive data
        2022: -0.3,  # D+0.3 from PVI analysis
        2024: 4.4    # R+4.4 from PVI analysis
    }
    
    print("\nYear  R Seats  D Seats  Other  Total  R%     Environment")
    print("-"*60)
    
    for year in sorted(results.keys()):
        r = results[year]['R']
        d = results[year]['D']
        other = results[year]['Other']
        total = results[year]['Total']
        r_pct = (r / total) * 100
        
        env = baselines.get(year, 'Unknown')
        if env is not None and env != 'Unknown':
            if env > 0:
                env_str = f"R+{env:.1f}"
            elif env < 0:
                env_str = f"D+{abs(env):.1f}"
            else:
                env_str = "Neutral"
        else:
            env_str = "Unknown"
        
        print(f"{year}  {r:^8} {d:^8} {other:^6} {total:^6} {r_pct:^6.1f}% {env_str:>12}")
    
    # Analyze the pattern
    print("\n" + "="*60)
    print("ANALYSIS OF RESULTS IN CURRENT DISTRICTS")
    print("="*60)
    
    # From our mapping exercise (using vote totals, not perfect but indicative)
    mapped_results = {
        2016: {'R': 205, 'D': 187, 'est_total': 392},
        2018: {'R': 159, 'D': 236, 'est_total': 395},
        2020: {'R': 194, 'D': 201, 'est_total': 395}
    }
    
    print("\nEstimated results if historical votes were cast in current districts:")
    print("(Note: These are estimates based on town-level vote aggregation)")
    print("\nYear  Est R  Est D  Total  R%     vs Actual R  Difference")
    print("-"*60)
    
    for year in [2016, 2018, 2020]:
        est_r = mapped_results[year]['R']
        est_d = mapped_results[year]['D']
        est_total = mapped_results[year]['est_total']
        est_r_pct = (est_r / est_total) * 100
        
        actual_r = results[year]['R']
        diff = est_r - actual_r
        
        print(f"{year}  {est_r:^6} {est_d:^6} {est_total:^6} {est_r_pct:^6.1f}%   {actual_r:^10} {diff:^+10}")
    
    # Key insights
    print("\n" + "="*60)
    print("KEY INSIGHTS")
    print("="*60)
    
    print("\n1. STRUCTURAL ADVANTAGE:")
    print("   - In 2022 (neutral environment), Republicans won 201-198")
    print("   - This suggests a ~3 seat Republican structural advantage")
    print("   - Democrats need approximately D+1 to D+2 environment to break even")
    
    print("\n2. ENVIRONMENTAL SENSITIVITY:")
    print("   - 2022 (neutral): 201R, 198D")
    print("   - 2024 (R+4.4): 222R, 178D")
    print("   - Swing: 21 seats shifted with 4.4 point environment change")
    print("   - Approximately 4.8 seats per environment point")
    
    print("\n3. COMPETITIVE RANGE:")
    print("   - In D+5 environment: Expect ~175R, 225D")
    print("   - In R+5 environment: Expect ~225R, 175D")
    print("   - Control can swing by ~50 seats based on political environment")
    
    # Calculate break-even point
    # At neutral (0), R has 201 seats
    # Need 200 seats for control
    # R loses ~4.8 seats per D point
    breakeven = 1 / 4.8
    print(f"\n4. BREAK-EVEN POINT:")
    print(f"   - Democrats need approximately D+{breakeven:.1f} environment for 50-50 split")
    print(f"   - Democrats need approximately D+{breakeven*2:.1f} environment for bare majority")

if __name__ == "__main__":
    analyze_actual_results()