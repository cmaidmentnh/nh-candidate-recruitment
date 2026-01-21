-- Add private challengers table for secret primary tracking
-- Run on 138.197.20.97 against candidate_recruitment database

-- Private challengers - stored like candidates but hidden until made public
CREATE TABLE IF NOT EXISTS secret_primary_challengers (
    id SERIAL PRIMARY KEY,
    target_id INTEGER REFERENCES secret_primary_targets(id) ON DELETE CASCADE,

    -- Candidate-like fields
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(50),
    address VARCHAR(255),
    city VARCHAR(100),
    zip VARCHAR(20),

    -- Voter file match
    voter_id VARCHAR(50),
    voter_data JSONB,  -- Store matched voter file data

    -- Status tracking
    status VARCHAR(50) DEFAULT 'potential',  -- potential, confirmed, filed, declined
    notes TEXT,

    -- Privacy control
    is_public BOOLEAN DEFAULT FALSE,
    made_public_at TIMESTAMP,
    made_public_by VARCHAR(255),

    -- Metadata
    created_by VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for lookups
CREATE INDEX IF NOT EXISTS idx_challenger_target ON secret_primary_challengers(target_id);
CREATE INDEX IF NOT EXISTS idx_challenger_public ON secret_primary_challengers(is_public);

-- Update targets table - remove old challenger fields (optional, can keep for backwards compat)
-- ALTER TABLE secret_primary_targets DROP COLUMN IF EXISTS challenger_name;
-- ALTER TABLE secret_primary_targets DROP COLUMN IF EXISTS challenger_status;
-- ALTER TABLE secret_primary_targets DROP COLUMN IF EXISTS challenger_contact;
