-- 2026 general election ballot, exactly as the Secretary of State published it.
-- Loaded from data/sos_general_2026/ by load_sos_general_ballot.py (idempotent, full replace).
-- This is the ballot of record for the public API; `filings` is our own intake record and is
-- reconciled against this table separately.
CREATE TABLE IF NOT EXISTS sos_general_ballot (
    id           serial PRIMARY KEY,
    election_year integer NOT NULL DEFAULT 2026,
    office       varchar(100) NOT NULL,
    district     varchar(50),             -- 'Belknap 2' for House; '1' style number otherwise; NULL if countywide/statewide
    county       varchar(50),             -- House: from the district; county offices: from the SoS list
    name         varchar(200) NOT NULL,   -- as printed on the ballot
    sos_party    varchar(10) NOT NULL,    -- DEM, REP, LIB, CON, IND, UND, CLA ... as the SoS prints it
    party        char(1) NOT NULL,        -- D, R, or I for everyone else
    town         varchar(100),            -- residence town; SoS list for House, filings match otherwise
    source_line  integer,
    loaded_at    timestamp NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sos_gb_office ON sos_general_ballot (election_year, office, district);
