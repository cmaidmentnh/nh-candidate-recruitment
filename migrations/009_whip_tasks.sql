-- 009_whip_tasks.sql — task list for the whip tool, modelled on sw_task.
BEGIN;

CREATE TABLE IF NOT EXISTS whip_tasks (
    id           SERIAL PRIMARY KEY,
    title        TEXT NOT NULL,
    detail       TEXT,
    candidate_id INTEGER REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    assigned_to  INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_by   INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_by_name VARCHAR(120),
    due_date     DATE,
    done         BOOLEAN NOT NULL DEFAULT false,
    done_at      TIMESTAMP,
    created_at   TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_whip_tasks_assigned ON whip_tasks (assigned_to, done);
CREATE INDEX IF NOT EXISTS idx_whip_tasks_cand     ON whip_tasks (candidate_id);

COMMIT;
