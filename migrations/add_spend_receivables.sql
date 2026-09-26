-- Money we expect to receive that has not reached the bank yet (e.g. a $200,000 transfer that
-- is promised but not sent). Counted toward the surplus/deficit until a bank import shows the
-- deposit, at which point the matcher marks it received and the bank balance takes over.
CREATE TABLE IF NOT EXISTS spend_receivable (
    id          SERIAL PRIMARY KEY,
    label       VARCHAR(160)  NOT NULL,
    amount      NUMERIC(12,2) NOT NULL,
    expected_on DATE,
    received    BOOLEAN       NOT NULL DEFAULT false,
    received_at DATE,
    match_hint  TEXT,            -- payer words to look for on the deposit line
    approx      BOOLEAN       NOT NULL DEFAULT false,
    bank_fp     VARCHAR(300),    -- fingerprint of the deposit row that settled it
    match_note  TEXT,
    active      BOOLEAN       NOT NULL DEFAULT true,
    created_by  VARCHAR(120),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);
