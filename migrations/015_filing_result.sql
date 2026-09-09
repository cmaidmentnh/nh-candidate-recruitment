-- 015_filing_result.sql
-- Record the 2026 primary outcome against each official filing.
--
-- Why a column rather than the party-flip trick: filings.party and candidates.party are
-- read by different pages, so a half-flip leaves a candidate visible somewhere, and
-- _link_to_existing_candidate (app.py) matches filings to candidates on party, so flipping
-- breaks future re-links. Deleting the row destroys the record of who filed. A result
-- column keeps the SOS filing intact and makes "still running" one queryable fact.

ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS result             varchar(10) NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS result_votes       integer,
    ADD COLUMN IF NOT EXISTS result_recorded_at timestamp,
    ADD COLUMN IF NOT EXISTS result_source      varchar(20),
    ADD COLUMN IF NOT EXISTS result_note        text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'filings_result_check'
    ) THEN
        ALTER TABLE filings
            ADD CONSTRAINT filings_result_check
            CHECK (result IN ('pending', 'won', 'lost', 'withdrawn'));
    END IF;
END $$;

-- Every roster query filters on (election_year, party) already; result joins that filter.
CREATE INDEX IF NOT EXISTS idx_filings_result
    ON filings (election_year, party, result);

COMMENT ON COLUMN filings.result IS
    'Primary outcome: pending (not yet decided), won (advances to the general), '
    'lost (defeated in the primary), withdrawn. Set from nh_elections.db via '
    'primary_results.py, confirmed by an admin.';
COMMENT ON COLUMN filings.result_votes IS
    'Vote total this candidate received in the primary, as counted when result was set.';
COMMENT ON COLUMN filings.result_source IS
    'computed = derived from results data alone; chris = confirmed or overridden by an admin.';
COMMENT ON COLUMN filings.result_note IS
    'Free text, e.g. "recount eligible, margin 4 votes" or the reason for an override.';
