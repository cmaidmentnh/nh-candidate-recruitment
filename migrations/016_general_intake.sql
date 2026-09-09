-- 016_general_intake.sql
-- What each November nominee already has, self-reported through the candidate portal.
--
-- URLs are NOT stored here: website, donation and Facebook links are written back to
-- `candidates` (website_url, donate_url, facebook_url) so they feed the auto-derived
-- columns on /progress and the public directory the same way an admin edit would.
-- Only the facts with nowhere else to live are stored on this table.

ALTER TABLE candidate_campaign_progress
    ADD COLUMN IF NOT EXISTS signs_have          boolean,
    ADD COLUMN IF NOT EXISTS lit_have            boolean,
    ADD COLUMN IF NOT EXISTS headshot_have       boolean,
    ADD COLUMN IF NOT EXISTS walkbooks_have      boolean,
    ADD COLUMN IF NOT EXISTS cash_on_hand        numeric(12,2),
    ADD COLUMN IF NOT EXISTS anticipated_raise   numeric(12,2),
    ADD COLUMN IF NOT EXISTS intake_notes        text,
    ADD COLUMN IF NOT EXISTS intake_submitted_at timestamp;

-- Existing columns reused rather than duplicated:
--   signs_count         how many signs they have
--   fundraising_amount  money raised so far
COMMENT ON COLUMN candidate_campaign_progress.cash_on_hand IS
    'Self-reported cash on hand at the general-election intake.';
COMMENT ON COLUMN candidate_campaign_progress.anticipated_raise IS
    'Self-reported estimate of how much MORE they expect to raise before November.';
COMMENT ON COLUMN candidate_campaign_progress.intake_submitted_at IS
    'When the candidate completed the post-primary intake form. NULL means no response yet, '
    'which is the follow-up list.';

CREATE INDEX IF NOT EXISTS idx_ccp_intake_submitted
    ON candidate_campaign_progress (intake_submitted_at);
