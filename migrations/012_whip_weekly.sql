-- 012_whip_weekly.sql
--
-- Candidate whip, rebuilt around the actual job: a whip owns a handful of
-- candidates and rings each of them once a week.
--
-- The previous attempt made an admin open and close a "round" before any whip
-- had work. Nobody ever opened a second one, so the tool showed "No round is
-- open" and sat empty. THE WEEK IS THE ROUND. Every Monday the whole roster
-- goes back to due, with no administrative act required from anyone.
--
-- Two tables:
--   whip_checkins    one row per candidate per week — the call
--   whip_field_log   an audit trail for whips correcting our tracking data
--
-- Assignment, last_contact_at, next_followup_at and the manual milestone
-- columns already live on candidate_campaign_progress (migrations 007/008/010)
-- and are reused as-is.
--
-- Apply: psql -h 127.0.0.1 -U postgres -d candidate_recruitment -f migrations/012_whip_weekly.sql

BEGIN;

-- ------------------------------------------------------------ the call
CREATE TABLE IF NOT EXISTS whip_checkins (
    id                SERIAL PRIMARY KEY,
    candidate_id      INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    week_start        DATE    NOT NULL,        -- Monday of the week this covers
    contacted_by      INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    contacted_by_name VARCHAR(120),
    contacted_at      TIMESTAMP NOT NULL DEFAULT now(),
    outcome           VARCHAR(16),   -- talked | voicemail | texted | emailed | no_answer
    status            VARCHAR(16),   -- rolling | needs_help | at_risk | not_running
    asks              TEXT[],        -- what they need from us
    ask_detail        TEXT,
    notes             TEXT,
    CONSTRAINT uniq_candidate_week UNIQUE (candidate_id, week_start)
);

CREATE INDEX IF NOT EXISTS idx_wc_week  ON whip_checkins (week_start DESC);
CREATE INDEX IF NOT EXISTS idx_wc_cand  ON whip_checkins (candidate_id, week_start DESC);
CREATE INDEX IF NOT EXISTS idx_wc_by    ON whip_checkins (contacted_by, week_start DESC);
CREATE INDEX IF NOT EXISTS idx_wc_stat  ON whip_checkins (status);

-- --------------------------------------------------------- corrections
-- A whip on the phone is the freshest source we have. When they correct what
-- the tracker says, the fix is written straight through to the master record
-- (candidates / candidate_campaign_progress) so it improves every other tool,
-- and the before/after lands here so a wrong "fix" can be traced and undone.
CREATE TABLE IF NOT EXISTS whip_field_log (
    id              SERIAL PRIMARY KEY,
    candidate_id    INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    field           VARCHAR(40) NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    changed_by      INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    changed_by_name VARCHAR(120),
    changed_at      TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wfl_cand ON whip_field_log (candidate_id, changed_at DESC);

-- ------------------------------------------------- the round experiment
-- Both tables were empty in production (0 rows, 1 never-reopened round).
-- Nothing outside the old whip.py ever referenced them.
DROP TABLE IF EXISTS whip_answers;
DROP TABLE IF EXISTS whip_rounds;

COMMIT;
