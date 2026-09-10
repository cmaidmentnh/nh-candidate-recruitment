-- 024_universe_plans.sql
-- The plan is now per UNIVERSE per district, not one universe per district.
--
-- Chris's model, 2026-09-10. Turnout tiers run on MIDTERM behaviour (2018 + 2022) because
-- 2026 is a midterm: A both, B one, C presidential only, D none.
--   gotv     = tier B or C, our Republicans (R primary ballots, or registered R with no
--              partisan primary history). The turnout job.
--   persuade = tier A or B, flip-floppers and voters with no partisan primary history,
--              excluding registered Democrats. The persuasion job.
-- Each universe gets its own piece counts and its own content, so district_spend_item is
-- re-keyed to include the universe. Existing rows are preserved as universe 'base', which is
-- the old modeled-R plan, so nothing Chris entered is lost.

ALTER TABLE district_spend_item ADD COLUMN IF NOT EXISTS universe varchar(20) NOT NULL DEFAULT 'base';
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='district_spend_item_pkey') THEN
    ALTER TABLE district_spend_item DROP CONSTRAINT district_spend_item_pkey;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='district_spend_item_pk2') THEN
    ALTER TABLE district_spend_item
      ADD CONSTRAINT district_spend_item_pk2 PRIMARY KEY (district_code, universe, tactic_key);
  END IF;
END $$;

-- Modelled universe sizes. Reference data, rebuilt from the voter file; holds no decisions.
CREATE TABLE IF NOT EXISTS district_model_universe (
    district_code varchar(50) NOT NULL,
    uni           varchar(20) NOT NULL,
    voters        integer NOT NULL,
    households    integer NOT NULL,
    cells         integer NOT NULL,
    PRIMARY KEY (district_code, uni)
);

-- Quantities are user decisions, so they get history like district_spend already has.
CREATE TABLE IF NOT EXISTS district_spend_item_history (
    id bigserial PRIMARY KEY,
    district_code varchar(50) NOT NULL, universe varchar(20), tactic_key varchar(40),
    qty numeric(12,2), changed_at timestamp NOT NULL DEFAULT now(), op varchar(10) NOT NULL
);
CREATE OR REPLACE FUNCTION log_spend_item() RETURNS trigger AS $$
BEGIN
    INSERT INTO district_spend_item_history (district_code, universe, tactic_key, qty, op)
    VALUES (OLD.district_code, OLD.universe, OLD.tactic_key, OLD.qty, lower(TG_OP));
    RETURN COALESCE(NEW, OLD);
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_spend_item_history ON district_spend_item;
CREATE TRIGGER trg_spend_item_history BEFORE UPDATE OR DELETE ON district_spend_item
    FOR EACH ROW EXECUTE FUNCTION log_spend_item();
