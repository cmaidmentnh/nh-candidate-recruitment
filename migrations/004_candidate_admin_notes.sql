-- Per-candidate admin assessment + notes for the survey tracker (super-admin only).
-- Distinct from candidate_surveys (which is per outside-group survey source).

CREATE TABLE IF NOT EXISTS candidate_admin_notes (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    assessment   VARCHAR(20),          -- Good / Bad / Indifferent (your own call)
    notes        TEXT,
    updated_at   TIMESTAMP DEFAULT now(),
    updated_by   VARCHAR(120)
);
