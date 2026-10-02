-- People who appear in a campaign ad but aren't in its Meta/StackAdapt campaign name.
-- Grafton 9's campaigns are named for MacRae alone, yet most of its creatives also feature
-- Donald McFarlane (Grafton 18 floterial). The portal's "My Ads" treats a row here like a
-- name match. Keyed by ws_campaign_ads.id (stable per media file), so a re-sync keeps it.
CREATE TABLE IF NOT EXISTS ws_campaign_ad_people_extra (
    ad_id      INTEGER NOT NULL,
    last_name  TEXT    NOT NULL,
    note       TEXT,
    created_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (ad_id, last_name)
);
