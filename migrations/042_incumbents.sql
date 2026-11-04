-- Who holds each House seat: the winners of a general, by the year they won it.
-- election_year = 2026 rows are owned by incumbents_sync.py, which rewrites them from
-- elections.nhhouse.gop every 10 minutes until the results are certified. Nothing else
-- should edit them by hand; fix the result on the results site instead.
-- status: won = called, unopposed or certified; leading = top of the count, not yet called.
CREATE TABLE IF NOT EXISTS incumbents (
    election_year integer     NOT NULL,
    district_code varchar(50) NOT NULL,
    name          varchar(200) NOT NULL,
    party         char(1)     NOT NULL,
    status        varchar(10) NOT NULL CHECK (status IN ('won', 'leading')),
    certified     boolean     NOT NULL DEFAULT FALSE,
    candidate_id  integer REFERENCES candidates(candidate_id) ON DELETE SET NULL,
    source        varchar(20) NOT NULL DEFAULT 'results',
    synced_at     timestamp   NOT NULL DEFAULT now(),
    PRIMARY KEY (election_year, district_code, name)
);
CREATE INDEX IF NOT EXISTS idx_incumbents_cid ON incumbents (election_year, candidate_id);

-- The 2024 winners, from the flag the 2026 cycle ran on, so CYCLE_YEAR=2026 still works.
INSERT INTO incumbents (election_year, district_code, name, party, status, candidate_id, source)
SELECT DISTINCT ON (c.candidate_id) 2024, ces.district_code, c.first_name || ' ' || c.last_name,
       c.party, 'won', c.candidate_id, 'legacy_flag'
FROM candidates c
JOIN candidate_election_status ces ON ces.candidate_id = c.candidate_id AND ces.election_year = 2024
WHERE c.incumbent AND ces.district_code IS NOT NULL
ORDER BY c.candidate_id
ON CONFLICT DO NOTHING;
