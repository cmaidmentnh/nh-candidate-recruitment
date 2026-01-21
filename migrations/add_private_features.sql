-- Migration: Add private features tables
-- Run with: psql $DATABASE_URL -f migrations/add_private_features.sql

-- Private Feature Access Control
-- Controls who can access which private features
CREATE TABLE IF NOT EXISTS private_feature_access (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
    feature_slug VARCHAR(50) NOT NULL,  -- e.g., 'secret_primaries', 'speaker_votes'
    granted_by VARCHAR(255) NOT NULL,
    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    UNIQUE(user_id, feature_slug)
);

CREATE INDEX IF NOT EXISTS idx_pfa_user ON private_feature_access(user_id);
CREATE INDEX IF NOT EXISTS idx_pfa_feature ON private_feature_access(feature_slug);

-- Secret Primary Campaigns
-- Each campaign represents a group's primary recruitment effort
CREATE TABLE IF NOT EXISTS secret_primary_campaigns (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    target_year INTEGER NOT NULL DEFAULT 2026,
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- Secret Primary Targets
-- Individual targets within each campaign
CREATE TABLE IF NOT EXISTS secret_primary_targets (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES secret_primary_campaigns(id) ON DELETE CASCADE,
    district_code VARCHAR(20) NOT NULL,  -- e.g., 'ROCK-01'
    incumbent_name VARCHAR(255),
    incumbent_party VARCHAR(10),
    challenger_name VARCHAR(255),
    challenger_status VARCHAR(50) DEFAULT 'recruiting',  -- recruiting, confirmed, declined, filed
    challenger_contact TEXT,
    notes TEXT,
    priority INTEGER DEFAULT 5,  -- 1-10, lower = higher priority
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_spt_campaign ON secret_primary_targets(campaign_id);
CREATE INDEX IF NOT EXISTS idx_spt_district ON secret_primary_targets(district_code);

-- Speaker Vote Tracking
-- Track commitments for Jason Osborne for Speaker
CREATE TABLE IF NOT EXISTS speaker_vote_tracking (
    id SERIAL PRIMARY KEY,
    legislator_name VARCHAR(255) NOT NULL,
    district_code VARCHAR(20),
    employee_number VARCHAR(20),  -- For linking to official data
    commitment_status VARCHAR(50) DEFAULT 'unknown',  -- committed, leaning_yes, unknown, leaning_no, opposed
    contacted_by VARCHAR(255),
    contacted_at TIMESTAMP,
    last_contact_at TIMESTAMP,
    notes TEXT,
    confidence_level INTEGER DEFAULT 5,  -- 1-10, how confident are we in their commitment
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(employee_number)
);

CREATE INDEX IF NOT EXISTS idx_svt_status ON speaker_vote_tracking(commitment_status);
CREATE INDEX IF NOT EXISTS idx_svt_district ON speaker_vote_tracking(district_code);

-- Speaker Vote Contact Log
-- Track all contacts/conversations about speaker vote
CREATE TABLE IF NOT EXISTS speaker_vote_contacts (
    id SERIAL PRIMARY KEY,
    tracking_id INTEGER REFERENCES speaker_vote_tracking(id) ON DELETE CASCADE,
    contacted_by VARCHAR(255) NOT NULL,
    contact_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    contact_method VARCHAR(50),  -- phone, in_person, text, email
    outcome VARCHAR(50),  -- positive, negative, neutral, no_answer
    notes TEXT,
    status_before VARCHAR(50),
    status_after VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_svc_tracking ON speaker_vote_contacts(tracking_id);
