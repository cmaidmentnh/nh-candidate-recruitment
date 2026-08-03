-- 013: separate "is a whip" from "role", so naming someone a whip no longer
-- demotes them out of the rest of the app.
--
-- Why: app.py:419 gates every admin screen on role = 'admin'. The whip roster
-- previously promoted people with UPDATE users SET role='whip', which silently
-- stripped an existing admin's access to the candidate dashboard, filters and
-- edit screens the moment you named them a whip. Nobody hit it only because
-- the roster page had never been used. A flag keeps the two ideas apart:
-- role says what you can reach, is_whip says whether you take a call list.

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_whip BOOLEAN NOT NULL DEFAULT FALSE;

-- Anyone already carrying the old role, and anyone already holding a list,
-- is a whip under the new scheme.
UPDATE users SET is_whip = TRUE WHERE role = 'whip';

UPDATE users u SET is_whip = TRUE
 WHERE EXISTS (SELECT 1 FROM candidate_campaign_progress p
                WHERE p.assigned_whip = u.user_id);

-- NOTE: role is deliberately left alone. role='whip' still means "whip only,
-- not an admin", which is the correct least-privilege level for someone whose
-- whole job is a call list. Promoting those rows to 'admin' here would hand
-- them the entire admin surface. is_whip answers "does this person take a
-- list"; role answers "what can they reach". They are different questions.

CREATE INDEX IF NOT EXISTS idx_users_is_whip ON users (is_whip) WHERE is_whip;
