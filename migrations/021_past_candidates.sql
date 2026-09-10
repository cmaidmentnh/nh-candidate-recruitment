-- 021_past_candidates.sql
-- The actual candidates and vote totals from the 2022 and 2024 generals, per district.
-- The aggregate seat counts in district_past_results answer "did we hold it"; this answers
-- "who ran and by how much", which is what you need when deciding whether to spend.
-- Reference data rebuilt from nh_elections.db; holds no user decisions.
CREATE TABLE IF NOT EXISTS district_past_candidates (
    district_code varchar(50) NOT NULL,
    year          smallint    NOT NULL,
    name          varchar(120) NOT NULL,
    party         char(1)     NOT NULL,
    votes         integer     NOT NULL,
    rank          smallint    NOT NULL,
    won           boolean     NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dpc_district ON district_past_candidates (district_code, year, rank);
