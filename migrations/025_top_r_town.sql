-- 025_top_r_town.sql
-- The town holding the most Republicans in each district, so the worklist is readable.
-- "Hillsborough 12" means nothing at a glance; "Hillsborough 12 · Merrimack" does.
-- 97 of 203 districts are a single town, where this is simply that town's name.
-- Reference data from the voter file; holds no decisions.
CREATE TABLE IF NOT EXISTS district_top_r_town (
    district_code varchar(50) PRIMARY KEY,
    town          varchar(80) NOT NULL,
    town_r        integer NOT NULL,
    district_r    integer NOT NULL,
    pct           smallint
);
