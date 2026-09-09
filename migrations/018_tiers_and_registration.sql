-- 018_tiers_and_registration.sql
-- Tier the districts first, work out tactics second. Chris's three tiers:
--   1 full effort, 2 needs real spend, 3 light touch. NULL means not yet triaged.
ALTER TABLE district_spend ADD COLUMN IF NOT EXISTS tier smallint;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='district_spend_tier_check') THEN
    ALTER TABLE district_spend ADD CONSTRAINT district_spend_tier_check
      CHECK (tier IS NULL OR tier IN (1,2,3));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_district_spend_tier ON district_spend (tier);

-- Straight registration counts, so the detail pane shows the actual party split
-- alongside the modelled universe. Registration is NOT the same as the buy universe:
-- modeled R includes undeclared voters who pull an R primary ballot.
CREATE TABLE IF NOT EXISTS district_registration (
    district_code varchar(50) PRIMARY KEY,
    reg_r int NOT NULL, reg_d int NOT NULL, reg_u int NOT NULL, reg_total int NOT NULL
);
