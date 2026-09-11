-- 030_materials_optout.sql
-- A candidate can refuse to appear in our materials.
--
-- Matt Coker (Belknap 2) wrote on 2026-09-10: "I respectfully request that my name, image,
-- likeness... not be used in any of your materials." There was no way to record that. The
-- only suppression we had was for EMAIL, which is a different thing: it stops us writing TO
-- someone, not printing ABOUT them. A candidate can want our mail and still refuse to be on
-- a card, or the reverse.
--
-- This is a consent record, so it is deliberately blunt: a flag, the date, and their own
-- words. Anything that puts a candidate's name on a printed piece, a slate card or a shared
-- page must check it.

ALTER TABLE candidates
    ADD COLUMN IF NOT EXISTS materials_optout      boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS materials_optout_at   timestamp,
    ADD COLUMN IF NOT EXISTS materials_optout_note text;

COMMENT ON COLUMN candidates.materials_optout IS
    'Candidate has asked not to appear in CTEHR materials: no mail, no palm card, no shared '
    'page. Separate from unsubscribed_email, which only stops us emailing them.';
COMMENT ON COLUMN candidates.materials_optout_note IS
    'Their own words, so the scope of what they asked for is never guessed at later.';

CREATE INDEX IF NOT EXISTS idx_candidates_materials_optout
    ON candidates (materials_optout) WHERE materials_optout;
