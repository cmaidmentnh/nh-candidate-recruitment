-- 034_meta_creative.sql
-- Keep our own copy of every Meta ad image.
--
-- meta_ads.thumbnail_url and image_url point at Meta's CDN and are SIGNED and SHORT LIVED: the
-- oe= parameter on the rows synced 2026-09-12 decodes to 2026-09-17, about four days. The
-- /meta/ page gets away with rendering them because the hourly sync keeps rewriting the URL.
-- Anything a person opens days later - a district's Ads tab, a page left in a browser tab, a
-- link someone was sent - shows broken images.
--
-- So the bytes are copied to our own S3 at sync time and everything downstream reads s3_url.
-- Same pattern as the palm card headshots in directory-headshots/.
--
-- sha256 is the dedupe key: three ads in one campaign usually share one image, and a re-sync
-- must not re-upload what we already hold.

CREATE TABLE IF NOT EXISTS meta_ad_creative (
    ad_id        varchar(64)  PRIMARY KEY,
    source_url   text,                       -- the Meta URL we fetched, for tracing only
    s3_url       text         NOT NULL,      -- ours, permanent, safe to render any time
    content_type varchar(80),
    bytes        integer,
    sha256       varchar(64),
    is_video     boolean      NOT NULL DEFAULT false,
    fetched_at   timestamp    NOT NULL DEFAULT now(),
    error        text                        -- set when a fetch failed, so it is not retried blindly
);
CREATE INDEX IF NOT EXISTS idx_meta_creative_sha ON meta_ad_creative (sha256);

COMMENT ON TABLE meta_ad_creative IS
    'Our own copy of each Meta ad image. Meta CDN URLs are signed and expire in about four '
    'days, so nothing user-facing may render them directly.';
