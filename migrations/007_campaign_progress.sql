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

-- Manual "has a voter list / walkbook" tick (covers lists emailed out, which aren't
-- otherwise logged); combined with portal walkbook_requests for the Walkbook column.
ALTER TABLE candidate_campaign_progress ADD COLUMN IF NOT EXISTS walkbook_done BOOLEAN DEFAULT FALSE;
-- Manual "consulted with Maidment" tick (covers consults done outside the /consult booking page);
-- combined with consult_requests for the Consult column.
ALTER TABLE candidate_campaign_progress ADD COLUMN IF NOT EXISTS consult_done BOOLEAN DEFAULT FALSE;
