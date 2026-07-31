-- 011_whip_rounds.sql
--
-- Work arrives as a ROUND. That is the one structural thing both working whip
-- tools share: whip.nhhouse.gop runs 15 survey rounds and lands 173-185 of 223
-- members every single time, because the job is finite and finishable -
-- "get my 20 answered before session". My previous attempt had no round, no
-- finish line and no fixed questions, and it sat empty.
--
-- Nothing goes out to candidates. The whip rings them and types the answers.
BEGIN;

CREATE TABLE IF NOT EXISTS whip_rounds (
    id        SERIAL PRIMARY KEY,
    title     TEXT NOT NULL,
    is_open   BOOLEAN NOT NULL DEFAULT true,
    opened_at TIMESTAMP NOT NULL DEFAULT now(),
    closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS whip_answers (
    id            SERIAL PRIMARY KEY,
    round_id      INTEGER NOT NULL REFERENCES whip_rounds(id) ON DELETE CASCADE,
    candidate_id  INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    answered_by   INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    answered_name VARCHAR(120),
    answered_at   TIMESTAMP NOT NULL DEFAULT now(),
    reached       BOOLEAN,
    campaigning   VARCHAR(12),   -- yes | barely | no | no_contact
    canvassing    VARCHAR(12),   -- yes | not_yet
    signs         VARCHAR(12),   -- yes | ordered | no
    raised        NUMERIC(12,2),
    needs         TEXT[],
    concern       VARCHAR(10),   -- fine | watch | problem
    notes         TEXT,
    CONSTRAINT uniq_round_candidate UNIQUE (round_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_wa_round ON whip_answers (round_id);
CREATE INDEX IF NOT EXISTS idx_wa_by    ON whip_answers (answered_by);
CREATE INDEX IF NOT EXISTS idx_wa_conc  ON whip_answers (concern);

-- superseded by whip_answers; it never held a real row
DROP TABLE IF EXISTS campaign_checkins;

INSERT INTO whip_rounds (title)
SELECT 'First Check-In'
WHERE NOT EXISTS (SELECT 1 FROM whip_rounds);

COMMIT;
