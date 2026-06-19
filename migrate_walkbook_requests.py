#!/usr/bin/env python3
"""Create the walkbook_requests table in the candidate_recruitment DB. Idempotent."""
import os
import psycopg2

DB_URL = os.environ.get('DATABASE_URL') or os.environ.get('CANDIDATE_DB_URL')
if not DB_URL:
    raise SystemExit('Set DATABASE_URL (candidate_recruitment Postgres).')

DDL = """
CREATE TABLE IF NOT EXISTS walkbook_requests (
    id              SERIAL PRIMARY KEY,
    candidate_id    INTEGER NOT NULL,
    candidate_name  VARCHAR(200),
    email           VARCHAR(255),
    district_code   VARCHAR(50),
    parties         VARCHAR(50),
    book_size       INTEGER DEFAULT 100,
    notes           TEXT,
    status          VARCHAR(20) DEFAULT 'new',
    created_at      TIMESTAMP DEFAULT NOW(),
    built_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_walkbook_requests_status ON walkbook_requests(status);
CREATE INDEX IF NOT EXISTS idx_walkbook_requests_candidate ON walkbook_requests(candidate_id);
"""

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
cur.execute(DDL)
conn.commit()
cur.close()
conn.close()
print('walkbook_requests table ready.')
