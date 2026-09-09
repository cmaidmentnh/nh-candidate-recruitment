-- 019_spend_history.sql
-- Keep every change to a district's plan. Written after a bulk auto-tier overwrote
-- hand-entered tiers with no way to recover them: there was no history, so the previous
-- values were simply gone. Manual work must never be silently destroyed again.

CREATE TABLE IF NOT EXISTS district_spend_history (
    id            bigserial PRIMARY KEY,
    district_code varchar(50) NOT NULL,
    tier          smallint,
    mask          smallint,
    include       boolean,
    notes         text,
    changed_by    varchar(100),
    changed_at    timestamp NOT NULL DEFAULT now(),
    op            varchar(10) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dsh_district ON district_spend_history (district_code, changed_at DESC);

CREATE OR REPLACE FUNCTION log_district_spend() RETURNS trigger AS $$
BEGIN
    -- Record the row as it was BEFORE the change, so the previous value is recoverable.
    IF TG_OP = 'UPDATE' THEN
        INSERT INTO district_spend_history (district_code, tier, mask, include, notes, changed_by, op)
        VALUES (OLD.district_code, OLD.tier, OLD.mask, OLD.include, OLD.notes, OLD.updated_by, 'update');
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO district_spend_history (district_code, tier, mask, include, notes, changed_by, op)
        VALUES (OLD.district_code, OLD.tier, OLD.mask, OLD.include, OLD.notes, OLD.updated_by, 'delete');
    END IF;
    RETURN COALESCE(NEW, OLD);
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_district_spend_history ON district_spend;
CREATE TRIGGER trg_district_spend_history
    BEFORE UPDATE OR DELETE ON district_spend
    FOR EACH ROW EXECUTE FUNCTION log_district_spend();
