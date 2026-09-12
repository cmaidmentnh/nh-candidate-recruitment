-- 033_meta_district_link.sql
-- Tie a Meta campaign to the district it is running in, so real ad spend shows up on the
-- spend planner next to what we planned.
--
-- CTEHR names district campaigns "Wallace | BE2 General | Meredith | Impressions | Sep-Nov
-- 2026". The county-and-number token in that name is the link. Issue campaigns like
-- "Property Tax Cap | Conversions | Aug 2026" carry no district and correctly map to nothing:
-- statewide spend must never be attributed to a district it did not run in.
--
-- The mapping is stored rather than parsed on every read, so a campaign whose name does not
-- follow the convention can be pointed at a district by hand and stay pointed.

CREATE TABLE IF NOT EXISTS meta_campaign_district (
    campaign_id   varchar(64)  NOT NULL,
    district_code varchar(50)  NOT NULL,
    source        varchar(16)  NOT NULL DEFAULT 'auto',   -- auto = read off the name
    campaign_name text,
    linked_by     varchar(120),
    linked_at     timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, district_code),
    CONSTRAINT meta_campaign_district_source_ck CHECK (source IN ('auto', 'manual'))
);
CREATE INDEX IF NOT EXISTS idx_mcd_district ON meta_campaign_district (district_code);

COMMENT ON TABLE meta_campaign_district IS
    'Which district a Meta campaign is running in. A campaign with no row here is statewide '
    'or unclassified and is deliberately left out of per-district actuals.';
