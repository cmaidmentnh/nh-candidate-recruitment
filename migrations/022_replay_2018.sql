-- 022_replay_2018.sql
-- What the 2018 general would have looked like on TODAY'S district lines.
--
-- 2018 ran on the 2012-2020 map, so this is not a result, it is a re-aggregation: each
-- town's actual 2018 State Rep votes, summed onto the current district that contains that
-- town. There are deliberately no candidate names, because the candidates ran in districts
-- that no longer exist. towns vs towns_with_data records the coverage, since 10 districts
-- have towns with no 2018 data and their share is correspondingly less reliable.
-- 2018 was a heavy Democratic year, so this is the stress test: a seat held today that goes
-- under 50% here is one a bad environment takes away.
CREATE TABLE IF NOT EXISTS district_2018_replay (
    district_code   varchar(50) PRIMARY KEY,
    r_votes         integer NOT NULL,
    d_votes         integer NOT NULL,
    r_share         numeric(5,1) NOT NULL,
    towns           smallint NOT NULL,
    towns_with_data smallint NOT NULL
);
