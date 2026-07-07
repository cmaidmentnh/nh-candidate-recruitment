-- Campaign Progress Tracker: admin-entered milestones that we don't derive from
-- other tables. One row per candidate; auto-derived signals (website, surveys,
-- walkbook, etc.) are computed live in campaign_progress.py, not stored here.
-- Apply: psql -h 127.0.0.1 -U postgres -d candidate_recruitment -f migrations/007_campaign_progress.sql

CREATE TABLE IF NOT EXISTS candidate_campaign_progress (
    candidate_id        INTEGER PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    fundraising_started BOOLEAN DEFAULT FALSE,
    fundraising_amount  NUMERIC(12,2),
    canvassing_started  BOOLEAN DEFAULT FALSE,
    signs_ordered       BOOLEAN DEFAULT FALSE,
    training_attended   BOOLEAN DEFAULT FALSE,
    stage_override      VARCHAR(30),
    notes               TEXT,
    updated_by          VARCHAR(120),
    updated_at          TIMESTAMP DEFAULT now()
);
