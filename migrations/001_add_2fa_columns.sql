-- Migration: Add Two-Factor Authentication columns
-- Run this on your database to enable 2FA functionality

-- Add 2FA columns to candidates table
ALTER TABLE candidates
ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(64),
ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN DEFAULT FALSE;

-- Add 2FA columns to users (admin) table
ALTER TABLE users
ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(64),
ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN DEFAULT FALSE;

-- Create index for faster lookups on totp_enabled
CREATE INDEX IF NOT EXISTS idx_candidates_totp_enabled ON candidates(totp_enabled) WHERE totp_enabled = TRUE;
CREATE INDEX IF NOT EXISTS idx_users_totp_enabled ON users(totp_enabled) WHERE totp_enabled = TRUE;

-- Verify the columns were added
SELECT 'candidates table columns:' as info;
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'candidates'
AND column_name IN ('totp_secret', 'totp_enabled');

SELECT 'users table columns:' as info;
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users'
AND column_name IN ('totp_secret', 'totp_enabled');
