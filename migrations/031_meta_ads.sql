-- 031_meta_ads.sql
-- Meta (Facebook / Instagram) ad monitoring and reporting, ported from the Goffstown CRM.
--
-- Two separate sources, two groups of tables.
--
-- 1. OUR ads, read from the Marketing API with an access token for an ad account we own.
--    Exact spend, impressions, clicks, per day and per campaign, plus every ad with its
--    picture and words. Tables: meta_ad_accounts, meta_insights, meta_reach, meta_ads,
--    meta_ad_sets.
--
-- 2. EVERYONE's political ads, read from the public Ad Library. Anyone can watch any Page
--    - ours, the NHDP's, an outside group's - but Meta only publishes spend and impressions
--    as a bracket ("$100-$199"), for the ad's whole life, never per day. Tables:
--    ad_watch_pages, ad_watch_ads, ad_watch_snapshots, ad_watch_meta_reports.
--
-- Access tokens pasted into the app are encrypted with AES-256-GCM before they are stored
-- (ENCRYPTION_KEY). Accounts that use the key on the server store a marker instead, so the
-- key itself never lands in the database.

-- ---------------------------------------------------------------------------------------
-- 1. Our own ad accounts
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS meta_ad_accounts (
    id               serial PRIMARY KEY,
    name             varchar(160) NOT NULL,
    account_id       varchar(40)  NOT NULL UNIQUE,   -- Meta's "act_1234567890"
    access_token_enc text         NOT NULL,          -- AES-GCM blob, or 'env:META_API_KEY'
    label            varchar(160),                   -- free text: whose ads these are
    currency         varchar(8)   NOT NULL DEFAULT 'USD',
    active           boolean      NOT NULL DEFAULT true,
    token_expires_at timestamptz,                    -- null: unknown, or never expires
    last_synced_at   timestamptz,
    last_sync_error  text,
    created_by       varchar(120),
    created_at       timestamptz  NOT NULL DEFAULT now()
);

COMMENT ON COLUMN meta_ad_accounts.access_token_enc IS
    'Encrypted access token, or the marker env:META_API_KEY for accounts read with the '
    'server key. Never shown again once saved.';

-- One row per account per day (level=account) and per campaign per day (level=campaign).
-- Money in whole dollars; Meta reports insights in whole units.
CREATE TABLE IF NOT EXISTS meta_insights (
    id              serial PRIMARY KEY,
    ad_account_id   integer     NOT NULL REFERENCES meta_ad_accounts(id) ON DELETE CASCADE,
    level           varchar(10) NOT NULL DEFAULT 'account',   -- account | campaign
    campaign_id     varchar(40) NOT NULL DEFAULT '',
    campaign_name   text,
    campaign_status varchar(40),
    date            date        NOT NULL,
    spend           numeric(12,2) NOT NULL DEFAULT 0,
    impressions     integer     NOT NULL DEFAULT 0,
    reach           integer     NOT NULL DEFAULT 0,
    clicks          integer     NOT NULL DEFAULT 0,
    link_clicks     integer     NOT NULL DEFAULT 0,
    cpm             numeric(12,4),
    cpc             numeric(12,4),
    ctr             numeric(12,4),
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ad_account_id, level, campaign_id, date)
);
CREATE INDEX IF NOT EXISTS idx_meta_insights_date ON meta_insights (date);

-- People reached across a whole window, as Meta counts them. Adding up the daily reach in
-- meta_insights counts the same person once per day they saw an ad; Meta will count them
-- once across a range if asked for that range in one go, so the sync asks per window.
CREATE TABLE IF NOT EXISTS meta_reach (
    id            serial PRIMARY KEY,
    ad_account_id integer     NOT NULL REFERENCES meta_ad_accounts(id) ON DELETE CASCADE,
    level         varchar(10) NOT NULL DEFAULT 'account',
    campaign_id   varchar(40) NOT NULL DEFAULT '',
    days          integer     NOT NULL,                     -- 7, 30 or 90
    reach         integer     NOT NULL DEFAULT 0,
    impressions   integer     NOT NULL DEFAULT 0,
    synced_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ad_account_id, level, campaign_id, days)
);

-- The creatives actually running: picture, headline, body, link, and the last 30 days of
-- results rolled up so the gallery needs no join.
CREATE TABLE IF NOT EXISTS meta_ads (
    id                 serial PRIMARY KEY,
    ad_account_id      integer     NOT NULL REFERENCES meta_ad_accounts(id) ON DELETE CASCADE,
    ad_id              varchar(40) NOT NULL,
    name               text        NOT NULL,
    status             varchar(40),
    effective_status   varchar(40),
    campaign_id        varchar(40),
    campaign_name      text,
    adset_id           varchar(40),
    adset_name         text,
    creative_id        varchar(40),
    thumbnail_url      text,
    image_url          text,
    video_id           varchar(40),
    title              text,
    body               text,
    link_url           text,
    spend              numeric(12,2) NOT NULL DEFAULT 0,
    impressions        integer     NOT NULL DEFAULT 0,
    reach              integer     NOT NULL DEFAULT 0,
    clicks             integer     NOT NULL DEFAULT 0,
    link_clicks        integer     NOT NULL DEFAULT 0,
    -- Last three days only. Meta leaves effective_status on ACTIVE after the schedule
    -- ends or the budget runs out, so zero recent impressions on an ACTIVE ad is how we
    -- tell "switched on" from "actually delivering".
    recent_spend       numeric(12,2) NOT NULL DEFAULT 0,
    recent_impressions integer     NOT NULL DEFAULT 0,
    first_delivered_on date,
    last_delivered_on  date,
    delivery_days      integer     NOT NULL DEFAULT 0,
    cpm                numeric(12,4),
    cpc                numeric(12,4),
    ctr                numeric(12,4),
    frequency          numeric(12,4),
    ad_created_at      timestamptz,
    synced_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ad_account_id, ad_id)
);
CREATE INDEX IF NOT EXISTS idx_meta_ads_spend ON meta_ads (spend);

-- Budgets live on the ad set, or on the campaign when campaign budget optimisation is on.
-- Mirrored so the gallery can say WHY an ad stopped. Dollars here; Meta reports cents.
CREATE TABLE IF NOT EXISTS meta_ad_sets (
    id                        serial PRIMARY KEY,
    ad_account_id             integer     NOT NULL REFERENCES meta_ad_accounts(id) ON DELETE CASCADE,
    adset_id                  varchar(40) NOT NULL,
    name                      text        NOT NULL,
    status                    varchar(40),
    effective_status          varchar(40),
    campaign_id               varchar(40),
    daily_budget              numeric(12,2),
    lifetime_budget           numeric(12,2),
    budget_remaining          numeric(12,2),
    campaign_daily_budget     numeric(12,2),
    campaign_lifetime_budget  numeric(12,2),
    campaign_budget_remaining numeric(12,2),
    campaign_objective        varchar(60),
    start_time                timestamptz,
    end_time                  timestamptz,
    optimization_goal         varchar(60),
    billing_event             varchar(60),
    bid_strategy              varchar(60),
    synced_at                 timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ad_account_id, adset_id)
);

-- ---------------------------------------------------------------------------------------
-- 2. The public Ad Library: pages we watch, on either side
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ad_watch_pages (
    id               serial PRIMARY KEY,
    name             varchar(160) NOT NULL,
    side             varchar(10)  NOT NULL DEFAULT 'oppose',  -- oppose | support
    office           varchar(120),                            -- e.g. "Rockingham 5", "NHDP"
    page_id          varchar(40),                             -- the Facebook Page that pays
    page_name        varchar(200),
    search_terms     text,                                    -- comma separated, when no page id
    notes            text,
    -- Meta's own exact figures, typed in from the Disclaimers panel on the page's Ad
    -- Library page. The API does not serve these. Cached from the newest report row.
    meta_total_spend numeric(12,2),
    meta_week_spend  numeric(12,2),
    meta_figures_on  date,
    active           boolean      NOT NULL DEFAULT true,
    last_synced_at   timestamptz,
    last_sync_error  text,
    created_by       varchar(120),
    created_at       timestamptz  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ad_watch_pages_page
    ON ad_watch_pages (page_id) WHERE page_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ad_watch_ads (
    id                serial PRIMARY KEY,
    watch_id          integer     NOT NULL REFERENCES ad_watch_pages(id) ON DELETE CASCADE,
    archive_id        varchar(40) NOT NULL,     -- the Ad Library's own id for the ad
    page_id           varchar(40),
    page_name         varchar(200),
    bylines           text,                     -- the "Paid for by" line
    currency          varchar(8)  NOT NULL DEFAULT 'USD',
    -- Meta publishes a bracket. Both ends are kept; a single number would invent precision.
    spend_lower       numeric(12,2) NOT NULL DEFAULT 0,
    spend_upper       numeric(12,2) NOT NULL DEFAULT 0,
    impressions_lower integer     NOT NULL DEFAULT 0,
    impressions_upper integer     NOT NULL DEFAULT 0,
    -- Best guess inside the bracket, priced off impressions. Written by the sync.
    spend_estimate    numeric(12,2) NOT NULL DEFAULT 0,
    audience_lower    integer,
    audience_upper    integer,
    body_text         text,
    link_title        text,
    link_caption      text,
    snapshot_url      text,                     -- token stripped before it is stored
    platforms         jsonb,
    regions           jsonb,
    demographics      jsonb,
    created_time      timestamptz,
    start_time        timestamptz,
    stop_time         timestamptz,              -- null: still running
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    synced_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (watch_id, archive_id)
);
CREATE INDEX IF NOT EXISTS idx_ad_watch_ads_spend ON ad_watch_ads (spend_lower);

-- The archive gives each ad a lifetime total, never a daily one. One row per page per day
-- holds the running total on that date; the gap between two days is what went out between.
CREATE TABLE IF NOT EXISTS ad_watch_snapshots (
    id                serial PRIMARY KEY,
    watch_id          integer     NOT NULL REFERENCES ad_watch_pages(id) ON DELETE CASCADE,
    date              date        NOT NULL,
    ads               integer     NOT NULL DEFAULT 0,
    active_ads        integer     NOT NULL DEFAULT 0,
    spend_lower       numeric(12,2) NOT NULL DEFAULT 0,
    spend_upper       numeric(12,2) NOT NULL DEFAULT 0,
    spend_estimate    numeric(12,2) NOT NULL DEFAULT 0,
    impressions_lower integer     NOT NULL DEFAULT 0,
    impressions_upper integer     NOT NULL DEFAULT 0,
    fetched_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (watch_id, date)
);
CREATE INDEX IF NOT EXISTS idx_ad_watch_snapshots_date ON ad_watch_snapshots (date);

-- Meta's exact "last 7 days" and all-time figures, read off the Disclaimers panel and kept
-- as a series. The only hard spend numbers this tool ever gets for someone else's page.
CREATE TABLE IF NOT EXISTS ad_watch_meta_reports (
    id          serial PRIMARY KEY,
    watch_id    integer     NOT NULL REFERENCES ad_watch_pages(id) ON DELETE CASCADE,
    read_on     date        NOT NULL,
    total_spend numeric(12,2),
    week_spend  numeric(12,2),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (watch_id, read_on)
);
