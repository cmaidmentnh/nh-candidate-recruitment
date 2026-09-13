-- Actual money charged by Meta, as opposed to spend Meta reports as delivered.
--
-- These cannot be fetched. The CTEHR ad account is funded by a credit card rather than a
-- credit line, so Meta issues receipts, not invoices, and every billing edge in the Graph API
-- is either gone or refuses: /transactions returns "nonexisting field" on every version from
-- v12 to v23, /business_invoices comes back empty because the account is not on invoicing
-- terms, and /extendedcredits says the application has no permission. Checked 2026-09-13.
--
-- So charges arrive from the CSV that Meta's Billing and Payments page exports, and this table
-- is where they land. reference is Meta's own transaction id, which makes re-importing the
-- same file harmless: an overlapping export updates the rows it already wrote rather than
-- duplicating them, so you can always just export the last 90 days again.

CREATE TABLE IF NOT EXISTS meta_billing_charge (
    reference       TEXT PRIMARY KEY,          -- Meta's transaction id
    account_id      TEXT,                      -- act_... where known
    charged_on      DATE NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,
    currency        TEXT DEFAULT 'USD',
    status          TEXT,                      -- paid, failed, pending
    payment_method  TEXT,                      -- "VISA *8523"
    product         TEXT,                      -- what Meta billed for
    note            TEXT,
    source          TEXT DEFAULT 'csv',        -- csv | manual
    raw             JSONB,                     -- the row as exported, so nothing is lost
    imported_by     TEXT,
    imported_at     TIMESTAMP DEFAULT now(),
    updated_at      TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_meta_charge_date ON meta_billing_charge (charged_on);
CREATE INDEX IF NOT EXISTS idx_meta_charge_acct ON meta_billing_charge (account_id);

-- A record of each import, so an unexpected total can be traced back to the file that made it.
CREATE TABLE IF NOT EXISTS meta_billing_import (
    id              SERIAL PRIMARY KEY,
    filename        TEXT,
    rows_seen       INTEGER,
    rows_written    INTEGER,
    rows_updated    INTEGER,
    first_charge    DATE,
    last_charge     DATE,
    total_amount    NUMERIC(12,2),
    imported_by     TEXT,
    imported_at     TIMESTAMP DEFAULT now()
);
