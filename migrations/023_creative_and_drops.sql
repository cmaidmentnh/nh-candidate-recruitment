-- 023_creative_and_drops.sql
-- Execution tracking for the spend plan: the creative that was produced, the drops that
-- carried it, and what each one actually cost.
--
-- Entirely additive. Nothing here touches district_spend, district_spend_item or
-- spend_tactic, which hold hand-entered decisions.

-- The creative library. Uploaded once, attachable to any number of district drops, so one
-- mail piece running in 40 districts is one upload rather than 40.
CREATE TABLE IF NOT EXISTS spend_creative (
    id           serial PRIMARY KEY,
    label        varchar(120) NOT NULL,
    tactic_key   varchar(40) REFERENCES spend_tactic(tactic_key),
    file_url     text NOT NULL,
    content_type varchar(100),
    file_size    integer,
    notes        text,
    uploaded_by  varchar(120),
    uploaded_at  timestamp NOT NULL DEFAULT now()
);

-- One row per district per tactic per sequence: "Rockingham 4, mail, drop 2".
-- planned_cost is a SNAPSHOT taken when the drop is created, so changing a district's
-- universe later never silently rewrites what a past drop was expected to cost.
CREATE TABLE IF NOT EXISTS district_drop (
    id             serial PRIMARY KEY,
    district_code  varchar(50) NOT NULL,
    tactic_key     varchar(40) NOT NULL REFERENCES spend_tactic(tactic_key),
    seq            smallint NOT NULL DEFAULT 1,
    label          varchar(120),
    creative_id    integer REFERENCES spend_creative(id) ON DELETE SET NULL,
    planned_date   date,
    delivered_date date,
    quantity       integer,
    planned_cost   numeric(12,2),
    actual_cost    numeric(12,2),
    invoice_number varchar(60),
    invoice_status varchar(12) NOT NULL DEFAULT 'unbilled',
    invoice_due    date,
    paid_at        date,
    notes          text,
    created_by     varchar(120),
    created_at     timestamp NOT NULL DEFAULT now()
);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='district_drop_invoice_status_check') THEN
    ALTER TABLE district_drop ADD CONSTRAINT district_drop_invoice_status_check
      CHECK (invoice_status IN ('unbilled','unpaid','paid'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_drop_district ON district_drop (district_code, planned_date);
CREATE INDEX IF NOT EXISTS idx_drop_creative ON district_drop (creative_id);
CREATE INDEX IF NOT EXISTS idx_drop_invoice  ON district_drop (invoice_status);
CREATE INDEX IF NOT EXISTS idx_drop_date     ON district_drop (planned_date);

-- The budget was a JavaScript literal in three places. One row, one source of truth.
CREATE TABLE IF NOT EXISTS spend_budget (
    key    varchar(40) PRIMARY KEY,
    label  varchar(120) NOT NULL,
    amount numeric(12,2) NOT NULL,
    notes  text
);
INSERT INTO spend_budget (key, label, amount, notes)
VALUES ('program', 'General election program', 600000, 'Chris''s figure, 2026-09-09')
ON CONFLICT (key) DO NOTHING;
