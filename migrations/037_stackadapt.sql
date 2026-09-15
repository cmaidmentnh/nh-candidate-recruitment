-- StackAdapt CTV delivery, shaped to mirror the Meta tables next to it.
--
-- The plan holds CTV as dollars in district_spend_item (tactic_key 'ctv'), and until now
-- nothing read back what those dollars actually bought. These three tables are the CTV
-- equivalents of meta_insights / meta_campaign_district / meta_ad_accounts, deliberately so:
-- anyone who can read the Meta pipeline can read this one without learning a second shape.
--
-- One difference worth stating. Meta plans CTV at a $45 CPM (spend_tactic.rate); these buys
-- bid $110, which is a ceiling rather than a price. Storing ecpm per day is the whole point:
-- it is the only way to find out what the inventory actually clears at.

CREATE TABLE IF NOT EXISTS stackadapt_insights (
    account_id      TEXT NOT NULL,
    advertiser_id   TEXT,
    level           TEXT NOT NULL DEFAULT 'campaign',   -- campaign | group | ad
    campaign_id     TEXT NOT NULL,
    campaign_name   TEXT,
    campaign_state  TEXT,                               -- ACTIVE, PENDING, ...
    campaign_status TEXT,                               -- CREATIVES_PENDING, ...
    group_id        TEXT,
    date            DATE NOT NULL,
    cost            NUMERIC(14,4) DEFAULT 0,            -- what StackAdapt calls cost
    impressions     BIGINT DEFAULT 0,
    clicks          BIGINT DEFAULT 0,
    ecpm            NUMERIC(12,4),                      -- the number the plan cannot predict
    ecpc            NUMERIC(12,4),
    ctr             NUMERIC(10,6),
    video_starts    BIGINT DEFAULT 0,
    video_completes BIGINT DEFAULT 0,
    frequency       NUMERIC(10,4),
    fetched_at      TIMESTAMP DEFAULT now(),
    PRIMARY KEY (campaign_id, level, date)
);

CREATE INDEX IF NOT EXISTS idx_sa_insights_date ON stackadapt_insights (date);
CREATE INDEX IF NOT EXISTS idx_sa_insights_camp ON stackadapt_insights (campaign_id);

-- Which district a campaign's money belongs to. Same contract as meta_campaign_district:
-- exactly one district per campaign, because two rows would count the same dollars twice.
-- A floterial candidate's campaign points at the BASE district that funds it, which is why
-- Drye's Sullivan 7 campaign is recorded against Sullivan 3.
CREATE TABLE IF NOT EXISTS stackadapt_campaign_district (
    campaign_id     TEXT PRIMARY KEY,
    district_code   TEXT NOT NULL,
    candidate_name  TEXT,
    campaign_name   TEXT,
    group_id        TEXT,
    source          TEXT DEFAULT 'manual',              -- manual | auto
    note            TEXT,
    linked_by       TEXT,
    linked_at       TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sa_link_district
    ON stackadapt_campaign_district (district_code);

-- Sync state, so a stale or failing pull is visible rather than silent.
CREATE TABLE IF NOT EXISTS stackadapt_accounts (
    account_id      TEXT PRIMARY KEY,
    advertiser_id   TEXT,
    name            TEXT,
    active          BOOLEAN DEFAULT true,
    last_attempt_at TIMESTAMP,
    last_synced_at  TIMESTAMP,
    last_sync_error TEXT,
    campaigns_seen  INTEGER,
    rows_written    INTEGER
);

INSERT INTO stackadapt_accounts (account_id, advertiser_id, name)
VALUES ('44108', '145826', 'Committee to Elect House Republicans')
ON CONFLICT (account_id) DO NOTHING;
