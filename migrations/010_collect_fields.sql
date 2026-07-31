-- 010_collect_fields.sql — quantify the milestones a whip collects.
-- A checkbox is a self-report. "$4,200 raised" or "3 of 5 signs delivered"
-- can be checked against a filing; "yes" cannot.
BEGIN;
ALTER TABLE candidate_campaign_progress
    ADD COLUMN IF NOT EXISTS doors_knocked  INTEGER,
    ADD COLUMN IF NOT EXISTS signs_count    INTEGER,
    ADD COLUMN IF NOT EXISTS training_name  VARCHAR(120);
COMMIT;
