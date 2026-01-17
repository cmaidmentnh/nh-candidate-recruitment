#!/usr/bin/env python3
"""
Comprehensive parser for 2024 NH election data
Based on the 2022 parser with adaptations for any 2024-specific formats
"""

import pandas as pd
import glob
import re
import csv
import json

def clean_vote(v):
    """Clean vote values"""
    if pd.isna(v) or str(v) == 'nan' or str(v).strip() == '':
        return 0
    try:
        return int(float(str(v).replace(',', '').replace(' ', '')))
    except:
        return 0

def parse_candidate(c):
    """Parse candidate from cell value"""
    if pd.isna(c) or not c:
        return None, None
    c = str(c).strip()
    
    # Skip non-candidates
    skip_terms = ['scatter', 'scattrer', 'write-ins', 'w-in', 'undervotes', 'overvotes', 
                  'recount', 'vacant', 'totals', 'blc', 'at. & gilm. ac. grant', 
                  "green's grant", "pinkham's grant", "wentworth's loc.", "dix's grant",
                  'fitzwilliam', 'court ordered recount', 'court ordered', 'kilkenny',
                  "bean's pur", "hadley's pur", "martin's loc", "low and burbank", 
                  "thomp and mes's pur", 'total']
    if c.lower() in skip_terms or 'w-in' in c.lower():
        return None, None
    
    # Handle special case of just party letter in recount headers
    if c.lower() in ['r', 'd', 'i', 'l']:
        return None, None
    
    if ',' not in c:
        return None, None
    
    parts = [p.strip() for p in c.split(',')]
    if len(parts) < 2:
        return None, None
    
    # Party is always the last part
    party_str = parts[-1].lower().strip()
    if party_str not in ['r', 'd', 'i', 'l', 'sgi']:
        return None, None
    
    party_map = {'r': 'R', 'd': 'D', 'i': 'I', 'l': 'L', 'sgi': 'I'}
    party = party_map[party_str]
    
    # Name is everything else
    name = ', '.join(parts[:-1])
    return name, party

def is_town_name(val):
    """Check if a value is likely a town name"""
    if pd.isna(val):
        return False
    val_str = str(val).strip().lower()
    
    # Skip these patterns
    skip_patterns = ['district', 'totals', 'recount', 'court ordered', 'at. & gilm', 
                     'blc', 'scatter', 'w-in', 'kilkenny', "bean's pur", "hadley's pur",
                     "martin's loc", "low and burbank", "thomp and mes's pur"]
    for pattern in skip_patterns:
        if pattern in val_str:
            return False
    
    # Must have some alphabetic characters
    if not any(c.isalpha() for c in val_str):
        return False
    
    # Skip if it's just a number
    if val_str.replace(' ', '').isdigit():
        return False
    
    return True

def parse_county_comprehensive(filepath):
    """Parse each county comprehensively"""
    county = re.search(r'house-([a-z]+)', filepath).group(1).title()
    df = pd.read_excel(filepath)
    
    print(f"\n{'='*80}")
    print(f"PARSING {county.upper()}")
    print(f"{'='*80}")
    
    all_results = []
    
    row = 0
    while row < len(df):
        first_col = str(df.iloc[row, 0]) if pd.notna(df.iloc[row, 0]) else ''
        
        # Look for district headers
        if (('District' in first_col and '(' in first_col) or 
            ('District No' in first_col) or 
            (first_col.startswith('District') and re.search(r'\d', first_col))):
            
            # Extract district number and seats
            dist_match = re.search(r'District.*?(\d+)', first_col)
            seat_match = re.search(r'\((\d+)\)', first_col)
            
            if not dist_match:
                row += 1
                continue
            
            dist_num = dist_match.group(1)
            seats = int(seat_match.group(1)) if seat_match else 1
            
            print(f"\nDistrict {dist_num} ({seats} seats):")
            
            # Find the end of this district
            end_row = len(df)
            for check_row in range(row + 1, len(df)):
                check_first = str(df.iloc[check_row, 0]) if pd.notna(df.iloc[check_row, 0]) else ''
                if (('District' in check_first and '(' in check_first) or 
                    ('District No' in check_first) or 
                    (check_first.startswith('District') and re.search(r'\d', check_first))):
                    end_row = check_row
                    break
            
            # Track all candidates and their columns
            all_candidates = {}  # (name, party) -> column
            candidate_vote_totals = {}  # (name, party) -> total votes
            
            # Parse all rows in this district
            scan_row = row
            while scan_row < end_row:
                # Check if this row has candidates
                row_candidates = []
                for col in range(1, len(df.columns)):
                    if col >= len(df.columns):
                        break
                    
                    # Check for candidate
                    name, party = parse_candidate(df.iloc[scan_row, col])
                    if name and party:
                        row_candidates.append((name, party, col))
                        all_candidates[(name, party)] = col
                
                # If we found candidates, look for their votes
                if row_candidates:
                    print(f"  Found candidates: {[f'{n}({p})' for n, p, c in row_candidates]}")
                    
                    # Special case: Check if next rows have unlabeled vote data
                    unlabeled_votes_found = False
                    if scan_row + 1 < end_row:
                        next_first = str(df.iloc[scan_row + 1, 0]) if pd.notna(df.iloc[scan_row + 1, 0]) else ''
                        # Check if the first column is empty or just a number
                        if not next_first.strip() or next_first.replace(',', '').replace(' ', '').isdigit():
                            # Check if there are numbers in the candidate columns
                            has_votes = False
                            for name, party, col in row_candidates:
                                if col < len(df.columns):
                                    val = clean_vote(df.iloc[scan_row + 1, col])
                                    if val > 0:
                                        has_votes = True
                                        break
                            
                            if has_votes:
                                unlabeled_votes_found = True
                                # Process these unlabeled vote rows
                                vote_row = scan_row + 1
                                row_count = 1
                                while vote_row < end_row:
                                    first_col_check = str(df.iloc[vote_row, 0]) if pd.notna(df.iloc[vote_row, 0]) else ''
                                    # Stop if we hit a labeled row
                                    if first_col_check.strip() and not first_col_check.replace(',', '').replace(' ', '').isdigit():
                                        break
                                    
                                    # Record votes
                                    for name, party, base_col in row_candidates:
                                        vote = clean_vote(df.iloc[vote_row, base_col])
                                        
                                        # Check for recount
                                        if base_col + 1 < len(df.columns):
                                            header = str(df.iloc[scan_row, base_col + 1]) if pd.notna(df.iloc[scan_row, base_col + 1]) else ''
                                            if 'recount' in header.lower():
                                                recount_vote = clean_vote(df.iloc[vote_row, base_col + 1])
                                                if recount_vote > 0:
                                                    vote = recount_vote
                                        
                                        if vote > 0:
                                            # Use a generic town name
                                            town_name = f"Unlabeled_Row_{row_count}"
                                            result = {
                                                'year': 2024,
                                                'county': county,
                                                'district': dist_num,
                                                'town': town_name,
                                                'candidate': name,
                                                'party': party,
                                                'votes': vote,
                                                'source': 'unlabeled_row'
                                            }
                                            all_results.append(result)
                                            
                                            # Track totals
                                            key = (name, party)
                                            if key not in candidate_vote_totals:
                                                candidate_vote_totals[key] = 0
                                            candidate_vote_totals[key] = vote  # Use the last value as total
                                    
                                    vote_row += 1
                                    row_count += 1
                    
                    # Look for votes in subsequent rows (standard format)
                    for vote_row in range(scan_row + 1, end_row):
                        first_col_vote = str(df.iloc[vote_row, 0]) if pd.notna(df.iloc[vote_row, 0]) else ''
                        
                        # Stop if we hit another candidate row
                        has_new_candidates = False
                        for col in range(1, len(df.columns)):
                            if col >= len(df.columns):
                                break
                            if parse_candidate(df.iloc[vote_row, col])[0]:
                                has_new_candidates = True
                                break
                        if has_new_candidates:
                            break
                        
                        # Process vote data
                        if is_town_name(first_col_vote):
                            town_name = first_col_vote.strip()
                            
                            # Record votes for each candidate
                            for name, party, base_col in row_candidates:
                                vote = clean_vote(df.iloc[vote_row, base_col])
                                
                                # Check for special columns (recount, BLC)
                                final_vote = vote
                                used_column = "regular"
                                
                                # Check next columns for recount/BLC
                                for offset in range(1, 3):  # Check up to 2 columns ahead
                                    if base_col + offset < len(df.columns):
                                        header = str(df.iloc[scan_row, base_col + offset]) if pd.notna(df.iloc[scan_row, base_col + offset]) else ''
                                        if 'recount' in header.lower():
                                            recount_vote = clean_vote(df.iloc[vote_row, base_col + offset])
                                            if recount_vote > 0:
                                                final_vote = recount_vote
                                                used_column = "recount"
                                        elif 'blc' in header.lower():
                                            blc_vote = clean_vote(df.iloc[vote_row, base_col + offset])
                                            if blc_vote > 0:
                                                final_vote += blc_vote  # BLC adds to total
                                                used_column = "regular+blc"
                                
                                if final_vote > 0:
                                    result = {
                                        'year': 2024,
                                        'county': county,
                                        'district': dist_num,
                                        'town': town_name,
                                        'candidate': name,
                                        'party': party,
                                        'votes': final_vote,
                                        'source': used_column
                                    }
                                    all_results.append(result)
                                    
                                    # Track totals
                                    key = (name, party)
                                    if key not in candidate_vote_totals:
                                        candidate_vote_totals[key] = 0
                                    candidate_vote_totals[key] += final_vote
                        
                        # Handle court ordered recount specifically
                        elif 'court ordered recount' in first_col_vote.lower():
                            # Court ordered recount is the final authority
                            for name, party, base_col in row_candidates:
                                vote = clean_vote(df.iloc[vote_row, base_col])
                                if vote > 0:
                                    key = (name, party)
                                    candidate_vote_totals[key] = vote  # Override with court ordered recount
                                    
                                    # Also add to results so it's used in final calculation
                                    result = {
                                        'year': 2024,
                                        'county': county,
                                        'district': dist_num,
                                        'town': 'Court Ordered Recount Total',
                                        'candidate': name,
                                        'party': party,
                                        'votes': vote,
                                        'source': 'court_ordered_recount'
                                    }
                                    all_results.append(result)
                        
                        # Handle special rows
                        elif 'total' in first_col_vote.lower():
                            # Use totals for verification or when no town data exists
                            for name, party, base_col in row_candidates:
                                vote = clean_vote(df.iloc[vote_row, base_col])
                                
                                # Check for recount column
                                for offset in range(1, 3):
                                    if base_col + offset < len(df.columns):
                                        header = str(df.iloc[scan_row, base_col + offset]) if pd.notna(df.iloc[scan_row, base_col + offset]) else ''
                                        if 'recount' in header.lower():
                                            recount_vote = clean_vote(df.iloc[vote_row, base_col + offset])
                                            if recount_vote > 0:
                                                vote = recount_vote
                                
                                # Only use totals if we don't have town-level data
                                key = (name, party)
                                if key not in candidate_vote_totals and vote > 0:
                                    result = {
                                        'year': 2024,
                                        'county': county,
                                        'district': dist_num,
                                        'town': 'District Total',
                                        'candidate': name,
                                        'party': party,
                                        'votes': vote,
                                        'source': 'total_line'
                                    }
                                    all_results.append(result)
                                    candidate_vote_totals[key] = vote
                        
                        elif 'recount' in first_col_vote.lower() and first_col_vote.lower() != 'court ordered recount':
                            # Handle standalone recount rows
                            for name, party, base_col in row_candidates:
                                vote = clean_vote(df.iloc[vote_row, base_col])
                                if vote > 0:
                                    # Update the total with recount value
                                    key = (name, party)
                                    candidate_vote_totals[key] = vote
                                    
                                    # Add a recount total record
                                    result = {
                                        'year': 2024,
                                        'county': county,
                                        'district': dist_num,
                                        'town': 'Recount Total',
                                        'candidate': name,
                                        'party': party,
                                        'votes': vote,
                                        'source': 'recount_row'
                                    }
                                    all_results.append(result)
                
                scan_row += 1
            
            # Report district summary
            if candidate_vote_totals:
                sorted_candidates = sorted(candidate_vote_totals.items(), key=lambda x: x[1], reverse=True)
                print(f"  Final totals:")
                for (name, party), votes in sorted_candidates:
                    print(f"    {name} ({party}): {votes}")
                
                # Determine winners
                winners = []
                if seats == 1 and len(sorted_candidates) >= 2:
                    # Check for tie
                    if sorted_candidates[0][1] == sorted_candidates[1][1]:
                        print(f"  TIE: {sorted_candidates[0][0][0]} and {sorted_candidates[1][0][0]} both have {sorted_candidates[0][1]} votes")
                        winners.append(('VACANT - TIE', 'Vacant', sorted_candidates[0][1]))
                    else:
                        winners.append((sorted_candidates[0][0][0], sorted_candidates[0][0][1], sorted_candidates[0][1]))
                else:
                    # Multi-seat district - take top N
                    for i in range(min(seats, len(sorted_candidates))):
                        winners.append((sorted_candidates[i][0][0], sorted_candidates[i][0][1], sorted_candidates[i][1]))
                
                print(f"  Winners: {[f'{n}({p})' for n, p, v in winners]}")
            
            row = end_row
        else:
            row += 1
    
    return all_results

# Main execution
if __name__ == "__main__":
    all_results = []
    
    files = sorted(glob.glob('nh_election_data/*2024*.xls*'))
    
    for filepath in files:
        county_results = parse_county_comprehensive(filepath)
        all_results.extend(county_results)
    
    # Write all town-level results
    with open('2024_nh_all_results_comprehensive.csv', 'w', newline='') as f:
        fieldnames = ['year', 'county', 'district', 'town', 'candidate', 'party', 'votes', 'source']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)
    
    print(f"\n{'='*80}")
    print(f"Wrote {len(all_results)} vote records to 2024_nh_all_results_comprehensive.csv")
    
    # Calculate winners from the comprehensive data
    district_totals = {}
    for result in all_results:
        if result['town'] not in ['District Total', 'Recount Total']:  # Use actual town data
            key = (result['county'], result['district'], result['candidate'], result['party'])
            if key not in district_totals:
                district_totals[key] = 0
            district_totals[key] += result['votes']
    
    # For districts with only totals, use those
    for result in all_results:
        if result['town'] in ['District Total', 'Recount Total']:
            key = (result['county'], result['district'], result['candidate'], result['party'])
            if key not in district_totals:
                district_totals[key] = result['votes']
            elif result['town'] == 'Recount Total':
                # Recount overrides
                district_totals[key] = result['votes']
    
    # Court ordered recounts override everything
    for result in all_results:
        if result['town'] == 'Court Ordered Recount Total':
            key = (result['county'], result['district'], result['candidate'], result['party'])
            district_totals[key] = result['votes']
    
    # Determine winners by district
    winners = []
    districts = {}
    
    # Group by district
    for (county, district, candidate, party), votes in district_totals.items():
        dist_key = (county, district)
        if dist_key not in districts:
            districts[dist_key] = []
        districts[dist_key].append((candidate, party, votes))
    
    # Determine expected seats (same as 2022 - 203 districts, 400 seats)
    expected_seats = {
        'Belknap': {1: 1, 2: 2, 3: 1, 4: 1, 5: 4, 6: 4, 7: 3, 8: 2},
        'Carroll': {1: 3, 2: 2, 3: 2, 4: 2, 5: 1, 6: 2, 7: 1, 8: 2},
        'Cheshire': {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2, 7: 1, 8: 1, 9: 1, 10: 2, 11: 1, 12: 1, 13: 1, 14: 1, 15: 2, 16: 1, 17: 1, 18: 2},
        'Coos': {1: 2, 2: 1, 3: 1, 4: 1, 5: 2, 6: 1, 7: 1},
        'Grafton': {1: 3, 2: 1, 3: 1, 4: 1, 5: 2, 6: 1, 7: 1, 8: 3, 9: 1, 10: 1, 11: 1, 12: 4, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1},
        'Hillsborough': {1: 4, 2: 7, 3: 3, 4: 3, 5: 3, 6: 3, 7: 3, 8: 3, 9: 3, 10: 3, 11: 3, 12: 8, 13: 6, 14: 2, 15: 2, 16: 2, 17: 2, 18: 2, 19: 2, 20: 2, 21: 2, 22: 2, 23: 2, 24: 2, 25: 2, 26: 2, 27: 1, 28: 2, 29: 4, 30: 3, 31: 1, 32: 3, 33: 2, 34: 3, 35: 2, 36: 2, 37: 1, 38: 2, 39: 2, 40: 4, 41: 3, 42: 3, 43: 4, 44: 2, 45: 1},
        'Merrimack': {1: 1, 2: 1, 3: 2, 4: 2, 5: 2, 6: 1, 7: 2, 8: 3, 9: 4, 10: 4, 11: 1, 12: 2, 13: 2, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1, 21: 1, 22: 1, 23: 1, 24: 1, 25: 1, 26: 1, 27: 2, 28: 1, 29: 1, 30: 1},
        'Rockingham': {1: 3, 2: 3, 3: 1, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1, 9: 2, 10: 3, 11: 4, 12: 2, 13: 10, 14: 2, 15: 2, 16: 7, 17: 4, 18: 2, 19: 1, 20: 3, 21: 1, 22: 1, 23: 1, 24: 2, 25: 9, 26: 1, 27: 1, 28: 1, 29: 4, 30: 2, 31: 2, 32: 1, 33: 1, 34: 1, 35: 1, 36: 1, 37: 1, 38: 1, 39: 1, 40: 1},
        'Strafford': {1: 2, 2: 3, 3: 1, 4: 3, 5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 4, 11: 3, 12: 4, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 3, 20: 1, 21: 3},
        'Sullivan': {1: 1, 2: 1, 3: 3, 4: 1, 5: 1, 6: 3, 7: 1, 8: 2}
    }
    
    # Calculate winners
    for (county, district), candidates in districts.items():
        sorted_candidates = sorted(candidates, key=lambda x: x[2], reverse=True)
        seats = expected_seats.get(county, {}).get(int(district), 1)
        
        # Check for ties in single-seat districts
        if seats == 1 and len(sorted_candidates) >= 2:
            if sorted_candidates[0][2] == sorted_candidates[1][2]:
                winners.append({
                    'county': county,
                    'district': district,
                    'candidate': 'VACANT - TIE',
                    'party': 'Vacant',
                    'votes': sorted_candidates[0][2]
                })
            else:
                winners.append({
                    'county': county,
                    'district': district,
                    'candidate': sorted_candidates[0][0],
                    'party': sorted_candidates[0][1],
                    'votes': sorted_candidates[0][2]
                })
        else:
            # Multi-seat districts
            for i in range(min(seats, len(sorted_candidates))):
                winners.append({
                    'county': county,
                    'district': district,
                    'candidate': sorted_candidates[i][0],
                    'party': sorted_candidates[i][1],
                    'votes': sorted_candidates[i][2]
                })
    
    # Write winners
    with open('2024_nh_winners_comprehensive.csv', 'w', newline='') as f:
        fieldnames = ['county', 'district', 'candidate', 'party', 'votes']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(winners, key=lambda x: (x['county'], int(x['district']))))
    
    # Summary
    party_counts = {}
    for winner in winners:
        party = winner['party']
        party_counts[party] = party_counts.get(party, 0) + 1
    
    print(f"\n{'='*80}")
    print("FINAL RESULTS - 2024 NH HOUSE ELECTION")
    print(f"{'='*80}")
    print(f"Total winners: {len(winners)}")
    for party in sorted(party_counts.keys()):
        print(f"{party}: {party_counts[party]}")
    
    print(f"\nREPUBLICANS WON: {party_counts.get('R', 0)} SEATS")
    print(f"DEMOCRATS WON: {party_counts.get('D', 0)} SEATS")