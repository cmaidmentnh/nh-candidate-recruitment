-- 027_spend_piece.sql
-- Execution tracking for the spend plan.
--
-- The plan says a district gets N mail pieces. Nothing until now recorded which piece, when
-- it dropped, what artwork went in it, or what it actually cost. district_drop tried to, one
-- district at a time, and was never used once: the real unit of work is a PIECE - one mail
-- piece, one text, one buy - that goes to many districts at the same time, on one invoice.
--
-- Quantity and cost are SNAPSHOTTED onto spend_piece_district when a district is added, so
-- widening a universe six weeks from now never rewrites what a piece that already dropped
-- cost. The plan is what we intend; a piece is what happened.

CREATE TABLE IF NOT EXISTS spend_piece (
    id             serial PRIMARY KEY,
    name           varchar(200) NOT NULL,
    tactic_key     varchar(50)  NOT NULL REFERENCES spend_tactic(tactic_key),
    universe       varchar(20)  NOT NULL DEFAULT 'persuade',
    drop_date      date,
    status         varchar(20)  NOT NULL DEFAULT 'draft',
    default_file_url     text,
    default_content_type varchar(120),
    notes          text,
    -- one invoice per piece: the vendor bills the whole run, not each district
    invoice_number varchar(80),
    invoice_amount numeric(12,2),
    invoice_status varchar(20) NOT NULL DEFAULT 'unbilled',
    invoice_due    date,
    paid_at        date,
    created_by     varchar(120),
    created_at     timestamp NOT NULL DEFAULT now(),
    updated_by     varchar(120),
    updated_at     timestamp NOT NULL DEFAULT now(),
    CONSTRAINT spend_piece_status_ck
        CHECK (status IN ('draft', 'scheduled', 'delivered')),
    CONSTRAINT spend_piece_invoice_ck
        CHECK (invoice_status IN ('unbilled', 'unpaid', 'paid'))
);
CREATE INDEX IF NOT EXISTS idx_spend_piece_drop ON spend_piece (drop_date);

-- Which districts a piece went to, and what it cost each of them at the moment it was added.
-- file_url NULL means this district used the piece's shared proof; a value means this
-- district got its own artwork.
CREATE TABLE IF NOT EXISTS spend_piece_district (
    piece_id      integer     NOT NULL REFERENCES spend_piece(id) ON DELETE CASCADE,
    district_code varchar(50) NOT NULL,
    file_url      text,
    content_type  varchar(120),
    quantity      numeric(14,2),
    planned_cost  numeric(12,2),
    added_at      timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (piece_id, district_code)
);
CREATE INDEX IF NOT EXISTS idx_spd_district ON spend_piece_district (district_code);

-- History before the bulk writer, not after it. A piece editor that can tick "all of Tier 1"
-- can untick it just as fast, and the snapshotted costs would go with it.
CREATE TABLE IF NOT EXISTS spend_piece_history (
    id         bigserial PRIMARY KEY,
    piece_id   integer,
    name       varchar(200),
    tactic_key varchar(50),
    universe   varchar(20),
    drop_date  date,
    status     varchar(20),
    invoice_number varchar(80),
    invoice_amount numeric(12,2),
    invoice_status varchar(20),
    changed_by varchar(120),
    changed_at timestamp NOT NULL DEFAULT now(),
    op         varchar(10) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sph_piece ON spend_piece_history (piece_id, changed_at DESC);

CREATE OR REPLACE FUNCTION log_spend_piece() RETURNS trigger AS $$
BEGIN
    INSERT INTO spend_piece_history (piece_id, name, tactic_key, universe, drop_date, status,
                                     invoice_number, invoice_amount, invoice_status,
                                     changed_by, op)
    VALUES (OLD.id, OLD.name, OLD.tactic_key, OLD.universe, OLD.drop_date, OLD.status,
            OLD.invoice_number, OLD.invoice_amount, OLD.invoice_status,
            OLD.updated_by, lower(TG_OP));
    RETURN COALESCE(NEW, OLD);
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_spend_piece_history ON spend_piece;
CREATE TRIGGER trg_spend_piece_history
    BEFORE UPDATE OR DELETE ON spend_piece
    FOR EACH ROW EXECUTE FUNCTION log_spend_piece();

CREATE TABLE IF NOT EXISTS spend_piece_district_history (
    id            bigserial PRIMARY KEY,
    piece_id      integer,
    district_code varchar(50),
    file_url      text,
    quantity      numeric(14,2),
    planned_cost  numeric(12,2),
    changed_at    timestamp NOT NULL DEFAULT now(),
    op            varchar(10) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spdh_piece ON spend_piece_district_history (piece_id, changed_at DESC);

CREATE OR REPLACE FUNCTION log_spend_piece_district() RETURNS trigger AS $$
BEGIN
    -- A cascade from deleting the whole piece is not interesting; a district removed from a
    -- live piece is. Both are recorded, and the piece history says which happened.
    INSERT INTO spend_piece_district_history
        (piece_id, district_code, file_url, quantity, planned_cost, op)
    VALUES (OLD.piece_id, OLD.district_code, OLD.file_url, OLD.quantity, OLD.planned_cost,
            lower(TG_OP));
    RETURN COALESCE(NEW, OLD);
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_spend_piece_district_history ON spend_piece_district;
CREATE TRIGGER trg_spend_piece_district_history
    BEFORE UPDATE OR DELETE ON spend_piece_district
    FOR EACH ROW EXECUTE FUNCTION log_spend_piece_district();
