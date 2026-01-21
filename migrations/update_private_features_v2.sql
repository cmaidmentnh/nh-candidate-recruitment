-- Migration: Update private features to use candidate_id
-- Run with: psql $DATABASE_URL -f migrations/update_private_features_v2.sql

-- Update speaker_vote_tracking to use candidate_id as primary key
ALTER TABLE speaker_vote_tracking
ADD COLUMN IF NOT EXISTS candidate_id INTEGER REFERENCES candidates(candidate_id) ON DELETE CASCADE;

-- Drop old unique constraint on employee_number, add one on candidate_id
ALTER TABLE speaker_vote_tracking DROP CONSTRAINT IF EXISTS speaker_vote_tracking_employee_number_key;
ALTER TABLE speaker_vote_tracking ADD CONSTRAINT speaker_vote_tracking_candidate_id_key UNIQUE (candidate_id);

CREATE INDEX IF NOT EXISTS idx_svt_candidate ON speaker_vote_tracking(candidate_id);

-- Update speaker_vote_contacts to use candidate_id instead of tracking_id
ALTER TABLE speaker_vote_contacts
ADD COLUMN IF NOT EXISTS candidate_id INTEGER REFERENCES candidates(candidate_id) ON DELETE CASCADE;

-- Make tracking_id nullable since we're moving to candidate_id
ALTER TABLE speaker_vote_contacts ALTER COLUMN tracking_id DROP NOT NULL;
ALTER TABLE speaker_vote_contacts DROP CONSTRAINT IF EXISTS speaker_vote_contacts_tracking_id_fkey;

CREATE INDEX IF NOT EXISTS idx_svc_candidate ON speaker_vote_contacts(candidate_id);

-- Update secret_primary_targets to link to incumbent candidate
ALTER TABLE secret_primary_targets
ADD COLUMN IF NOT EXISTS incumbent_candidate_id INTEGER REFERENCES candidates(candidate_id);

CREATE INDEX IF NOT EXISTS idx_spt_incumbent ON secret_primary_targets(incumbent_candidate_id);
