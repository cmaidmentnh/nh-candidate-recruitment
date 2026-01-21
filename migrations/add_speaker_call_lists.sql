-- Add call list functionality to speaker vote tracking
-- Run on 138.197.20.97 against candidate_recruitment database

-- Add assigned caller to speaker vote tracking
ALTER TABLE speaker_vote_tracking
    ADD COLUMN IF NOT EXISTS assigned_caller VARCHAR(255);

-- Index for filtering by assigned caller
CREATE INDEX IF NOT EXISTS idx_speaker_vote_assigned ON speaker_vote_tracking(assigned_caller);
