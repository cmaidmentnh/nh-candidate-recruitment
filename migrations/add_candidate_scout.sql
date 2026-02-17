-- Migration: Add Candidate Scout tables
-- Run with: PGPASSWORD=postgres123 psql -h 127.0.0.1 -U postgres candidate_recruitment -f migrations/add_candidate_scout.sql

-- Scout Prospects: One row per discovered potential candidate
CREATE TABLE IF NOT EXISTS scout_prospects (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(50),
    address VARCHAR(255),
    city VARCHAR(100),
    zip VARCHAR(20),
    county VARCHAR(50),
    occupation VARCHAR(255),
    employer VARCHAR(255),
    voter_id VARCHAR(50),
    voter_party VARCHAR(10),
    district_code VARCHAR(30),
    composite_score INTEGER DEFAULT 0,
    review_status VARCHAR(30) DEFAULT 'new',
    priority VARCHAR(10) DEFAULT 'medium',
    promoted_candidate_id INTEGER REFERENCES candidates(candidate_id),
    promoted_at TIMESTAMP,
    promoted_by VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(255) DEFAULT 'system',
    UNIQUE(first_name, last_name, city)
);

CREATE INDEX IF NOT EXISTS idx_scout_district ON scout_prospects(district_code);
CREATE INDEX IF NOT EXISTS idx_scout_status ON scout_prospects(review_status);
CREATE INDEX IF NOT EXISTS idx_scout_score ON scout_prospects(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_scout_name ON scout_prospects(last_name, first_name);

-- Scout Signals: Evidence that makes someone a prospect
CREATE TABLE IF NOT EXISTS scout_signals (
    id SERIAL PRIMARY KEY,
    prospect_id INTEGER REFERENCES scout_prospects(id) ON DELETE CASCADE,
    source_type VARCHAR(30) NOT NULL,
    signal_date DATE,
    title VARCHAR(500),
    detail TEXT,
    url VARCHAR(1000),
    fec_committee VARCHAR(255),
    fec_amount DECIMAL(10,2),
    signal_score INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_signal_prospect ON scout_signals(prospect_id);
CREATE INDEX IF NOT EXISTS idx_signal_source ON scout_signals(source_type);

-- Scout Scans: Track when data scans were run
CREATE TABLE IF NOT EXISTS scout_scans (
    id SERIAL PRIMARY KEY,
    scan_type VARCHAR(30) NOT NULL,
    status VARCHAR(20) DEFAULT 'running',
    parameters JSONB,
    prospects_found INTEGER DEFAULT 0,
    prospects_new INTEGER DEFAULT 0,
    signals_added INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    run_by VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_scan_type ON scout_scans(scan_type);

-- Scout Contacts: Outreach log per prospect
CREATE TABLE IF NOT EXISTS scout_contacts (
    id SERIAL PRIMARY KEY,
    prospect_id INTEGER REFERENCES scout_prospects(id) ON DELETE CASCADE,
    contacted_by VARCHAR(255) NOT NULL,
    contact_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    contact_method VARCHAR(50),
    outcome VARCHAR(50),
    notes TEXT,
    status_before VARCHAR(30),
    status_after VARCHAR(30)
);

CREATE INDEX IF NOT EXISTS idx_scout_contact_prospect ON scout_contacts(prospect_id);

-- Scout District Targets: Precomputed priority districts
CREATE TABLE IF NOT EXISTS scout_district_targets (
    id SERIAL PRIMARY KEY,
    district_code VARCHAR(30) NOT NULL UNIQUE,
    county_name VARCHAR(50),
    towns TEXT,
    seat_count INTEGER DEFAULT 1,
    confirmed_count INTEGER DEFAULT 0,
    empty_seats INTEGER DEFAULT 0,
    pvi DECIMAL(5,2),
    pvi_rating VARCHAR(20),
    priority_tier INTEGER DEFAULT 3,
    prospect_count INTEGER DEFAULT 0,
    last_scanned_at TIMESTAMP,
    notes TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sdt_priority ON scout_district_targets(priority_tier);
CREATE INDEX IF NOT EXISTS idx_sdt_empty ON scout_district_targets(empty_seats DESC);
