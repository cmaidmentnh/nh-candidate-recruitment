-- Weekly candidate digest: curated events queue, per-newsletter unsubscribes, send log.

CREATE TABLE IF NOT EXISTS digest_events (
    id                 SERIAL PRIMARY KEY,
    title              TEXT NOT NULL,
    category           VARCHAR(40) DEFAULT 'Event',   -- Event / Training / Deadline / Resource / Other
    event_date         DATE,
    event_time         VARCHAR(60),
    location           TEXT,
    url                TEXT,
    description        TEXT,
    submitted_by_name  VARCHAR(150),
    submitted_by_email VARCHAR(255),
    status             VARCHAR(20) DEFAULT 'pending', -- pending / approved / rejected / sent / archived
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    reviewed_by        VARCHAR(150),
    reviewed_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_digest_events_status ON digest_events(status);

-- Opt-out of the digest only; recipient stays on the main distro for everything else.
CREATE TABLE IF NOT EXISTS digest_unsubscribes (
    email           VARCHAR(255) PRIMARY KEY,
    unsubscribed_at TIMESTAMPTZ DEFAULT NOW(),
    source          VARCHAR(50) DEFAULT 'link'
);

CREATE TABLE IF NOT EXISTS digest_sends (
    id              SERIAL PRIMARY KEY,
    subject         TEXT,
    intro           TEXT,
    event_ids       INTEGER[],
    recipient_count INTEGER DEFAULT 0,
    sent_count      INTEGER DEFAULT 0,
    failed_count    INTEGER DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'sending',     -- sending / complete / failed
    sent_by         VARCHAR(150),
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);
