-- 014_match_api_keys.sql — API keys for the public candidate cross-reference API.
-- Keys are stored hashed; the plaintext is shown once at issue time and never again.
BEGIN;

CREATE TABLE IF NOT EXISTS match_api_keys (
    key_id        SERIAL PRIMARY KEY,
    key_hash      TEXT NOT NULL UNIQUE,
    key_prefix    VARCHAR(16) NOT NULL,
    org_name      VARCHAR(200) NOT NULL,
    contact_email VARCHAR(200),
    active        BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    created_by    VARCHAR(120),
    last_used_at  TIMESTAMP,
    call_count    INTEGER NOT NULL DEFAULT 0,
    rows_matched  BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_match_api_keys_prefix ON match_api_keys (key_prefix);
CREATE INDEX IF NOT EXISTS idx_match_api_keys_active ON match_api_keys (active);

COMMIT;
