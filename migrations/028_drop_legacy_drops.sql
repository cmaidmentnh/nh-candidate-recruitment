-- 028_drop_legacy_drops.sql
-- Remove district_drop and spend_creative, replaced by spend_piece.
--
-- Both were fully built - schema, CRUD routes, UI - and never used once. The reason is the
-- model: they made a DROP the unit, one district at a time, so a mail piece going to fifty
-- districts meant fifty rows, fifty uploads and fifty invoices for one job. spend_piece makes
-- the piece the unit and the districts its members, which is how the work is actually bought.
--
-- Safe because both tables are empty. The guard below makes that a fact rather than a memory:
-- if anything was written between writing this and running it, the migration stops.

DO $$
DECLARE n_drops int; n_creative int;
BEGIN
    SELECT count(*) INTO n_drops FROM district_drop;
    SELECT count(*) INTO n_creative FROM spend_creative;
    IF n_drops > 0 OR n_creative > 0 THEN
        RAISE EXCEPTION 'Not empty: district_drop has % rows, spend_creative has %. '
                        'Migrate them onto spend_piece before dropping.', n_drops, n_creative;
    END IF;
END $$;

DROP TABLE IF EXISTS district_drop;
DROP TABLE IF EXISTS spend_creative;
