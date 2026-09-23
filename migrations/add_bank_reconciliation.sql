-- Bank reconciliation for the spend plan topline (2026-09-23).
-- Chris pastes the TD Bank account screen each morning; posted rows are kept, pending rows are
-- replaced wholesale on every import because the bank's pending list is a snapshot.

CREATE TABLE IF NOT EXISTS bank_txn (
    id             SERIAL PRIMARY KEY,
    account        VARCHAR(20)   NOT NULL DEFAULT 'x6004',
    txn_date       DATE          NOT NULL,
    pending        BOOLEAN       NOT NULL DEFAULT false,
    kind           VARCHAR(60),
    description    TEXT          NOT NULL,
    amount         NUMERIC(12,2) NOT NULL,          -- signed: money out is negative
    balance        NUMERIC(12,2),                   -- running balance, posted rows only
    category       VARCHAR(40),
    category_set_by VARCHAR(120),                   -- NULL = set by rule, else who changed it
    fingerprint    VARCHAR(300)  NOT NULL UNIQUE,
    imported_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bank_txn_date ON bank_txn (txn_date);

CREATE TABLE IF NOT EXISTS bank_snapshot (
    id           SERIAL PRIMARY KEY,
    account      VARCHAR(20)   NOT NULL DEFAULT 'x6004',
    captured_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    captured_by  VARCHAR(120),
    available    NUMERIC(12,2),
    beginning    NUMERIC(12,2),
    pending      NUMERIC(12,2)
);

-- Monthly overhead that keeps running to an end date. The topline counts what is left of each.
CREATE TABLE IF NOT EXISTS spend_recurring (
    id        SERIAL PRIMARY KEY,
    label     VARCHAR(120)  NOT NULL,
    monthly   NUMERIC(12,2) NOT NULL,
    end_date  DATE          NOT NULL,
    notes     TEXT,
    active    BOOLEAN       NOT NULL DEFAULT true
);
