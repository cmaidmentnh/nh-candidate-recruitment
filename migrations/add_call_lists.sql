-- Add call list functionality to secret primaries
-- Run on 138.197.20.97 against candidate_recruitment database

-- Call lists table - define call lists that can be assigned to targets
CREATE TABLE IF NOT EXISTS secret_primary_call_lists (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES secret_primary_campaigns(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    assigned_to VARCHAR(255),  -- email of user assigned to make calls
    created_by VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Add call list assignment to targets
ALTER TABLE secret_primary_targets
    ADD COLUMN IF NOT EXISTS call_list_id INTEGER REFERENCES secret_primary_call_lists(id) ON DELETE SET NULL;

-- Add assigned caller to targets (direct assignment without call list)
ALTER TABLE secret_primary_targets
    ADD COLUMN IF NOT EXISTS assigned_caller VARCHAR(255);

-- Contact log for primary targets
CREATE TABLE IF NOT EXISTS secret_primary_contacts (
    id SERIAL PRIMARY KEY,
    target_id INTEGER REFERENCES secret_primary_targets(id) ON DELETE CASCADE,
    contacted_by VARCHAR(255),
    contact_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    contact_method VARCHAR(50),  -- phone, in_person, text, email
    outcome VARCHAR(50),  -- positive, neutral, negative, no_answer
    notes TEXT,
    status_before VARCHAR(50),
    status_after VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_primary_contacts_target ON secret_primary_contacts(target_id);
CREATE INDEX IF NOT EXISTS idx_primary_targets_call_list ON secret_primary_targets(call_list_id);
