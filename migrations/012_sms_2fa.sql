-- 012_sms_2fa.sql — SMS as a second factor alongside TOTP.
-- Codes are stored hashed, never in clear, and expire.
BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS twofa_phone        VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS twofa_sms_enabled  BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS sms_code_hash      VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS sms_code_expires   TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS sms_code_attempts  SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS sms_last_sent      TIMESTAMP;

COMMIT;
