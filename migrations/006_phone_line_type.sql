-- Phone line-type enrichment: store Amazon Pinpoint phone-validation results
-- (line type + carrier) for candidate phone numbers so we know which can receive SMS.
-- Apply: psql -h 127.0.0.1 -U postgres -d candidate_recruitment -f migrations/006_phone_line_type.sql

ALTER TABLE candidates ADD COLUMN IF NOT EXISTS phone1_type VARCHAR(20);
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS phone1_carrier VARCHAR(120);
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS phone2_type VARCHAR(20);
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS phone2_carrier VARCHAR(120);
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS phone_validated_at TIMESTAMP;
