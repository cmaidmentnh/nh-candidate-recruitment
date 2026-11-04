"""The election cycle the recruitment side works on.

CYCLE is the general we are recruiting for; PRIOR is the one before it, whose
winners are the sitting members (the `incumbents` table, kept in step with the
results site by incumbents_sync.py). Set CYCLE_YEAR in .env to move every
recruitment page at once.

The money side (spend plan, bank recon, Meta/StackAdapt billing, GOTV, absentee)
is NOT on this clock: it settles the 2026 program and keeps its own year.
"""
import os
from datetime import date, timedelta

CYCLE = int(os.environ.get('CYCLE_YEAR') or 2028)
PRIOR = CYCLE - 2


def general_election_day(year):
    """NH general: the Tuesday after the first Monday in November."""
    d = date(year, 11, 1)
    first_monday = d + timedelta(days=(0 - d.weekday()) % 7)
    return first_monday + timedelta(days=1)


def primary_day(year):
    """NH state primary: the second Tuesday in September."""
    d = date(year, 9, 1)
    first_tuesday = d + timedelta(days=(1 - d.weekday()) % 7)
    return first_tuesday + timedelta(days=7)


ELECTION_DAY = general_election_day(CYCLE)
PRIMARY_DAY = primary_day(CYCLE)

# Is this candidate a sitting member? A SQL fragment for "c" aliased candidates.
IS_INCUMBENT_SQL = ("EXISTS (SELECT 1 FROM incumbents i WHERE i.candidate_id = c.candidate_id "
                    "AND i.election_year = %d)" % PRIOR)
