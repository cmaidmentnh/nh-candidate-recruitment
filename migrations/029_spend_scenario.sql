-- 029_spend_scenario.sql
-- Named versions of the whole spend plan.
--
-- The program budget moved between $600k, $644k, $664k and $618k inside one day, and each
-- move overwrote the one before it: district_spend is keyed on district_code alone, so there
-- has only ever been one plan. Nobody could put a version in front of the board, try a
-- cheaper one and go back.
--
-- A scenario is a full copy of district_spend and district_spend_item at a moment, not a
-- branch. Restoring one replaces the live plan, and the live plan is copied to a scenario of
-- its own first, so a restore is never a one-way door.

CREATE TABLE IF NOT EXISTS spend_scenario (
    id         serial PRIMARY KEY,
    name       varchar(120) NOT NULL,
    note       text,
    total      numeric(14,2),
    districts  integer,
    seats      integer,
    auto       boolean NOT NULL DEFAULT false,   -- taken automatically before a restore
    created_by varchar(120),
    created_at timestamp NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spend_scenario_district (
    scenario_id   integer     NOT NULL REFERENCES spend_scenario(id) ON DELETE CASCADE,
    district_code varchar(50) NOT NULL,
    mask     smallint,
    include  boolean,
    notes    text,
    tier     smallint,
    PRIMARY KEY (scenario_id, district_code)
);

CREATE TABLE IF NOT EXISTS spend_scenario_item (
    scenario_id   integer     NOT NULL REFERENCES spend_scenario(id) ON DELETE CASCADE,
    district_code varchar(50) NOT NULL,
    universe      varchar(20) NOT NULL,
    tactic_key    varchar(50) NOT NULL,
    qty           numeric(12,2),
    rate_override numeric(12,4),
    PRIMARY KEY (scenario_id, district_code, universe, tactic_key)
);
CREATE INDEX IF NOT EXISTS idx_ssi_scenario ON spend_scenario_item (scenario_id);
