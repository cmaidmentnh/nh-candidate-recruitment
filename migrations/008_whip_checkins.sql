-- 008_whip_checkins.sql
--
-- Turns /progress from a read-only matrix into a whip workflow.
--
-- The existing tracker holds 5 rows for a 365-candidate cohort with zero
-- milestones ever ticked, because nobody owns a candidate and there is nowhere
-- to record a conversation. The Speaker-race whip tool, same database, has 365
-- rows, 60 assignments and 107 logged contacts. This ports that shape across:
-- an owner per candidate, and an event log instead of permanent booleans.
--
-- Additive only. Nothing existing is dropped or rewritten.

BEGIN;

-- ---------------------------------------------------------------- assignment
ALTER TABLE candidate_campaign_progress
    ADD COLUMN IF NOT EXISTS assigned_whip    INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS last_contact_at  TIMESTAMP,
    ADD COLUMN IF NOT EXISTS next_followup_at DATE,
    ADD COLUMN IF NOT EXISTS needs            TEXT[];

CREATE INDEX IF NOT EXISTS idx_ccp_assigned ON candidate_campaign_progress (assigned_whip);
CREATE INDEX IF NOT EXISTS idx_ccp_contact  ON candidate_campaign_progress (last_contact_at);

-- ------------------------------------------------------------- the event log
-- Mirrors speaker_whip_contacts, which is the shape that actually gets used.
-- Milestone answers are stored per check-in rather than only as current state,
-- so "they said fundraising was starting three weeks ago and it still hasn't"
-- becomes answerable.
CREATE TABLE IF NOT EXISTS campaign_checkins (
    id                SERIAL PRIMARY KEY,
    candidate_id      INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    contacted_by      INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    contacted_by_name VARCHAR(120),
    contacted_at      TIMESTAMP NOT NULL DEFAULT now(),
    method            VARCHAR(20),      -- call | text | email | in_person | voicemail
    reached           BOOLEAN,
    needs             TEXT[],
    notes             TEXT,
    fundraising       VARCHAR(12),      -- yes | not_yet | planning
    canvassing        VARCHAR(12),
    signs             VARCHAR(12),
    training          VARCHAR(12)
);

CREATE INDEX IF NOT EXISTS idx_checkins_candidate
    ON campaign_checkins (candidate_id, contacted_at DESC);
CREATE INDEX IF NOT EXISTS idx_checkins_by
    ON campaign_checkins (contacted_by, contacted_at DESC);

-- --------------------------------------------------------------- whip role
-- Every existing account is role='admin', so introducing 'whip' changes the
-- behaviour of exactly nobody until Chris assigns it.
COMMENT ON COLUMN users.role IS 'admin | whip';

COMMIT;
