-- 017_spend_planner.sql
-- District-by-district general election spend planning: pick tactics, pick how many,
-- see the cost. Replaces the district_plan approach, which could only tag channels and
-- carried no quantities, so it could never produce a dollar figure.

-- Universe sizes per district, per segment combination.
-- `mask` is a bitmask over five MUTUALLY EXCLUSIVE voter segments:
--   1  r_reliable   modeled R who voted the 2024 general
--   2  r_dropoff    modeled R who did not
--   4  und_voter    undeclared, not modeled R, voted 2022 or 2024
--   8  und_dropoff  undeclared, not modeled R, voted neither
--   16 dem          registered D or D primary voter
-- Modeled R = registered REP or an R state primary ballot in 2020/2022/2024.
-- Every combination is stored precomputed because households OVERLAP between segments:
-- one house can hold a reliable R and a drop-off R, so summing segments overstates a
-- mail buy by about 10%. Reading the row for the exact mask avoids that.
CREATE TABLE IF NOT EXISTS district_universe (
    district_code varchar(50) NOT NULL,
    mask          smallint    NOT NULL,
    voters        integer     NOT NULL,
    households    integer     NOT NULL,
    cells         integer     NOT NULL,
    PRIMARY KEY (district_code, mask)
);

-- What each tactic costs and what it is charged against.
--   per_household  cost = qty x households x rate   (qty = number of mail drops)
--   per_cell       cost = qty x cells x rate        (qty = number of sends)
--   dollars        cost = qty                       (rate is the CPM, for impressions)
--   per_unit       cost = qty x rate                (palm cards, signs, calls, doors)
CREATE TABLE IF NOT EXISTS spend_tactic (
    tactic_key  varchar(40) PRIMARY KEY,
    label       varchar(80) NOT NULL,
    unit        varchar(20) NOT NULL,
    rate        numeric(12,4),
    qty_label   varchar(40),
    grp         varchar(20) NOT NULL,
    sort_order  integer NOT NULL DEFAULT 100,
    active      boolean NOT NULL DEFAULT true
);

INSERT INTO spend_tactic (tactic_key, label, unit, rate, qty_label, grp, sort_order) VALUES
    ('mail',       'Mail',              'per_household', 0.45,  'drops',        'mail',    10),
    ('mms',        'MMS text',          'per_cell',      0.05,  'sends',        'text',    20),
    ('meta',       'Meta',              'dollars',      18.00,  'dollars',      'digital', 30),
    ('ctv',        'Streaming / CTV',   'dollars',      45.00,  'dollars',      'digital', 40),
    ('display',    'Display',           'dollars',       9.00,  'dollars',      'digital', 50),
    ('palm_cards', 'Palm cards',        'per_unit',      NULL,  'cards',        'field',   60),
    ('signs',      'Yard signs',        'per_unit',      NULL,  'signs',        'field',   70),
    ('gotv_calls', 'GOTV calls',        'per_unit',      NULL,  'calls',        'field',   80),
    ('doors',      'Paid door knocks',  'per_unit',      NULL,  'doors',        'field',   90)
ON CONFLICT (tactic_key) DO NOTHING;

-- One row per district: which universe is being bought, plus notes.
CREATE TABLE IF NOT EXISTS district_spend (
    district_code varchar(50) PRIMARY KEY,
    mask          smallint NOT NULL DEFAULT 3,   -- default: modeled R (reliable + drop-off)
    include       boolean  NOT NULL DEFAULT false,
    notes         text,
    updated_by    varchar(100),
    updated_at    timestamp DEFAULT now()
);

-- One row per district per tactic that has a quantity.
CREATE TABLE IF NOT EXISTS district_spend_item (
    district_code varchar(50) NOT NULL,
    tactic_key    varchar(40) NOT NULL REFERENCES spend_tactic(tactic_key),
    qty           numeric(12,2) NOT NULL DEFAULT 0,
    rate_override numeric(12,4),
    PRIMARY KEY (district_code, tactic_key)
);

CREATE INDEX IF NOT EXISTS idx_dsi_tactic ON district_spend_item (tactic_key);

COMMENT ON TABLE district_universe IS
    'Precomputed from the voter file on 138.197.36.143 (election_data), which is the fresh '
    'copy; the recruitment server''s own voter tables are stale. Rebuild with '
    'scratchpad build_universe.sql + build_combos.sql when the checklist is refreshed.';
COMMENT ON COLUMN district_spend.mask IS
    'Which segment combination this district is being bought against. Join district_universe '
    'on (district_code, mask) for the exact household and cell counts.';
