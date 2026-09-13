-- Candidate intake: public submissions from electhouserepublicans.com/candidates
-- Run: psql -h 127.0.0.1 -U postgres -d candidate_recruitment -f migrations/002_candidate_intake.sql

CREATE TABLE IF NOT EXISTS intake_verifications (
    verification_id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    code VARCHAR(6) NOT NULL,
    token VARCHAR(128),
    attempts INTEGER DEFAULT 0,
    verified_at TIMESTAMP,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intake_verifications_email ON intake_verifications(LOWER(email));
CREATE INDEX IF NOT EXISTS idx_intake_verifications_token ON intake_verifications(token);

CREATE TABLE IF NOT EXISTS intake_submissions (
    submission_id SERIAL PRIMARY KEY,
    verification_id INTEGER REFERENCES intake_verifications(verification_id),
    email VARCHAR(255) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    phone1 VARCHAR(50),
    phone2 VARCHAR(50),
    address VARCHAR(255),
    city VARCHAR(100),
    zip VARCHAR(20),
    town VARCHAR(100),
    district_code VARCHAR(50),
    facebook VARCHAR(500),
    twitter_x VARCHAR(500),
    instagram VARCHAR(500),
    website VARCHAR(500),
    notes TEXT,
    headshot_url VARCHAR(1000),
    photo_urls JSONB DEFAULT '[]'::jsonb,
    matched_candidate_id INTEGER REFERENCES candidates(candidate_id),
    auto_applied BOOLEAN DEFAULT FALSE,
    status VARCHAR(20) DEFAULT 'pending',
    reviewed_by VARCHAR(255),
    reviewed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_intake_submissions_status ON intake_submissions(status);
