-- 032_meta_settings.sql
-- Meta tokens pasted in the browser, and a record of every sync attempt.
--
-- The people who run this app cannot always reach the server's .env: the box is Chris's, main
-- auto-deploys, and the person holding the Meta token is not the person holding root. So the
-- token can be pasted on /meta/settings instead. It is stored AES-256-GCM encrypted, under
-- ENCRYPTION_KEY when that is set and otherwise under a key derived from the app's own
-- SECRET_KEY, and it is never shown again once saved. A value here wins over the environment.
--
-- last_attempt_at lets the in-app hourly sync tell "synced an hour ago" from "has been failing
-- every ten minutes since lunch": last_synced_at only moves on success.

CREATE TABLE IF NOT EXISTS meta_settings (
    key        varchar(60) PRIMARY KEY,      -- META_ADS_TOKEN | META_AD_LIBRARY_TOKEN
    value_enc  text        NOT NULL,         -- AES-GCM blob, same shape as meta_ad_accounts.access_token_enc
    hint       varchar(12),                  -- last 4 characters, so two pastes can be told apart
    updated_by varchar(120),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE meta_ad_accounts ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz;
ALTER TABLE ad_watch_pages   ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz;
