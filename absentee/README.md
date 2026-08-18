# Weekly absentee-ballot candidate mailer

Turns the SOS "STATE PRIMARY ABSENTEE BALLOT" spreadsheet into per-district
CSVs of Republican-ballot requesters and emails each Republican House
candidate their district's list (From: info@electhouserepublicans.com via SES).

The state refreshes the file regularly (Scott Maltzie shares it via Drive);
rerun on every new file:

    python3 run_pipeline.py --xlsx "~/Downloads/STATE PRIMARY ABSENTEE BALLOT AS OF MM-DD-YYYY.xlsx" --asof YYYY-MM-DD
    python3 send_absentee_blast.py --run runs/YYYY-MM-DD --asof "DD Month"                     # dry run
    python3 send_absentee_blast.py --run runs/YYYY-MM-DD --asof "DD Month" --draft-to chris@maidmentnh.com
    python3 send_absentee_blast.py --run runs/YYYY-MM-DD --asof "DD Month" --send              # live, resumable

Pipeline: flatten xlsx -> voter history + phones/districts from the secondary
droplet (election_data) -> partisan lean scoring -> House districts from
town/ward (nh-election-results/nh_elections.db, incl. floterials) -> CRM
emails -> per-district REP-ballot CSVs -> send plan from candidate_recruitment
filings (R State Rep, usable non-dead email; unsubscribed included per Chris).
Copy deliverable CSVs to ~/Desktop/absentee-2026-primary-<date>/.

The email copy in absentee_email.py is the version Chris approved 2026-08-10
(purely informational, no deadlines, no tactical advice). Don't change the
copy without his sign-off.
