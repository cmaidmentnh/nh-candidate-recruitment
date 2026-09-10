-- 020_past_results.sql
-- 2022 and 2024 general election results per district, so a district page shows how the
-- seat has actually behaved rather than only a modelled PVI.
--
-- Both cycles ran on the CURRENT 2022-2030 map, so these map straight across with no
-- re-aggregation. Pseudo-candidates (Write-Ins, Undervotes, Overvotes) are excluded before
-- ranking, or seat counts come out wrong. With them excluded the totals reproduce the known
-- chamber: 222 R in 2024. 2022 sums to 401 because one district ties at the cutoff.
-- Reference data, rebuilt from nh_elections.db; holds no user decisions.
CREATE TABLE IF NOT EXISTS district_past_results (
    district_code     varchar(50) NOT NULL,
    year              smallint    NOT NULL,
    seats             smallint    NOT NULL,
    r_seats           smallint    NOT NULL,
    d_seats           smallint    NOT NULL,
    r_votes           integer     NOT NULL,
    d_votes           integer     NOT NULL,
    last_winner_votes integer,
    first_loser_votes integer,
    PRIMARY KEY (district_code, year)
);
