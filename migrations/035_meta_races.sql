-- 035_meta_races.sql
-- Which race a Meta campaign is for: say how sure we are, and remember "no race".
--
-- 033 links a campaign to a district when its name carries the county-and-number token.
-- That is one clue. The ad text names towns and candidates too, and those are clues as
-- well - a campaign called "Wallace | Meredith | Impressions" is Belknap 2 even with no
-- "BE2" in it. Every clue is scored and the scores are kept, so a link made by the machine
-- says how sure it was and on what, and anything under the bar is put to a person instead
-- of guessed.
--
-- "No race" is an answer too. A statewide issue campaign must not be asked about every hour,
-- so the decision that it belongs to no district is written down.

ALTER TABLE meta_campaign_district
    ADD COLUMN IF NOT EXISTS confidence smallint,   -- 0-100; NULL on rows from before 035
    ADD COLUMN IF NOT EXISTS evidence   text;       -- one line: what it matched on

COMMENT ON COLUMN meta_campaign_district.confidence IS
    'How sure the automatic match was, 0-100. 100 when a person set it. Under 90 is never '
    'written automatically; those campaigns are asked about on the Meta ads page.';

CREATE TABLE IF NOT EXISTS meta_campaign_norace (
    campaign_id   varchar(64) PRIMARY KEY,
    campaign_name text,
    decided_by    varchar(120),
    decided_at    timestamp NOT NULL DEFAULT now()
);

COMMENT ON TABLE meta_campaign_norace IS
    'Campaigns a person has said belong to no district: statewide, an issue, a test. Kept so '
    'the question is asked once.';
