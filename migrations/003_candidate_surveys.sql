-- Candidate survey status tracking (super-admin only).
-- Tracks how R House candidates score on outside-group surveys (AFP, etc.).

CREATE TABLE IF NOT EXISTS candidate_surveys (
    id             SERIAL PRIMARY KEY,
    survey_org     VARCHAR(40)  NOT NULL DEFAULT 'AFP',
    candidate_id   INTEGER      REFERENCES candidates(candidate_id) ON DELETE SET NULL,
    candidate_name VARCHAR(120) NOT NULL,
    district       VARCHAR(60),
    rating         TEXT,                 -- free text: Good / Bad / Not Great / notes; blank = not yet returned
    notes          TEXT,
    created_at     TIMESTAMP    DEFAULT now(),
    updated_at     TIMESTAMP    DEFAULT now(),
    updated_by     VARCHAR(120),
    UNIQUE (survey_org, candidate_name, district)
);

CREATE INDEX IF NOT EXISTS idx_candidate_surveys_org      ON candidate_surveys (survey_org);
CREATE INDEX IF NOT EXISTS idx_candidate_surveys_cand     ON candidate_surveys (candidate_id);
