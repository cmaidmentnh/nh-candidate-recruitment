-- Cloud cost monitor: the Google Cloud VMs Ross rents to render After Effects, and what they cost.
--
-- Nothing here holds a credential. The VM list is read live from Google on every check; these
-- tables only remember what the check saw, so that
--   1. a warning goes out ONCE per VM run per threshold, not every ten minutes, and
--   2. "estimated spend today" still counts a VM that ran this morning and has since stopped.
--
-- The app runs this file itself on start (cost_monitor.ensure_tables). Everything is
-- IF NOT EXISTS, so it is safe to run again.

-- One row per run: one VM from one start to its stop. Google restarts the clock
-- (lastStartTimestamp) on every start, so (resource_id, started_at) names a run exactly.
-- resource_id is the instance's numeric id, not its name: a deleted VM's name can be reused.
CREATE TABLE IF NOT EXISTS cost_monitor_runs (
    source          TEXT        NOT NULL DEFAULT 'gcp',   -- only gcp in v1
    resource_id     TEXT        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    name            TEXT,
    location        TEXT,                                 -- zone, e.g. us-east1-c
    machine_type    TEXT,
    gpus            TEXT,                                 -- "1x L4", or NULL
    windows         BOOLEAN,
    rate_per_hour   NUMERIC(10,4),                        -- ESTIMATE; NULL means rate unknown
    stopped_at      TIMESTAMPTZ,                          -- set once the VM is seen stopped
    last_seen_at    TIMESTAMPTZ NOT NULL,
    last_status     TEXT,
    PRIMARY KEY (source, resource_id, started_at)
);

CREATE INDEX IF NOT EXISTS idx_cost_runs_seen ON cost_monitor_runs (last_seen_at);

-- Every warning sent. alert_key is what makes a warning once-only:
--   gcp:<instance id>:<run start>:hours:3   one per run per hour threshold
--   day:2026-09-23:usd:50                   one per day per dollar threshold
-- A row is written before the email goes and removed again if the email fails, so a
-- failed send is retried on the next check instead of being lost.
CREATE TABLE IF NOT EXISTS cost_monitor_alerts (
    id              SERIAL      PRIMARY KEY,
    alert_key       TEXT        NOT NULL UNIQUE,
    kind            TEXT        NOT NULL,                 -- hours | usd
    source          TEXT,
    resource_id     TEXT,
    resource_name   TEXT,
    run_started_at  TIMESTAMPTZ,
    message         TEXT,
    sent_to         TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row: when the check last ran and how it went, so a check that has quietly stopped
-- running shows on the page instead of looking like "nothing is on".
CREATE TABLE IF NOT EXISTS cost_monitor_state (
    id              INTEGER     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_check_at   TIMESTAMPTZ,
    last_ok_at      TIMESTAMPTZ,
    last_error      TEXT,
    running_count   INTEGER,
    est_today       NUMERIC(10,2),
    last_alert_error TEXT
);

INSERT INTO cost_monitor_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
