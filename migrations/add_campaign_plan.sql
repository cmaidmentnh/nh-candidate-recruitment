-- Campaign "Battle Plan" — one strategic plan row per NH House district.
CREATE TABLE IF NOT EXISTS district_plan (
    district_code VARCHAR(50) PRIMARY KEY,
    bucket        VARCHAR(40) DEFAULT 'unassigned',
    channels      TEXT[]      DEFAULT '{}',
    priority      INTEGER,                      -- 1 (high) .. 3 (low)
    notes         TEXT,
    updated_by    VARCHAR(100),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Seed one row per district, with a default posture bucket derived from PVI.
INSERT INTO district_plan (district_code, bucket, updated_by)
SELECT d.full_district_code,
       CASE UPPER(d.pvi_rating)
            WHEN 'SAFE GOP'   THEN 'hold_safe_r'
            WHEN 'LIKELY GOP' THEN 'defend'
            WHEN 'LEAN GOP'   THEN 'defend'
            WHEN 'SWING'      THEN 'battleground'
            WHEN 'LEAN DEM'   THEN 'target'
            WHEN 'LIKELY DEM' THEN 'target'
            WHEN 'SAFE DEM'   THEN 'pass_safe_d'
            ELSE 'unassigned'
       END AS bucket,
       'pvi-seed'
FROM (SELECT full_district_code, MAX(pvi_rating) AS pvi_rating
      FROM districts GROUP BY full_district_code) d
ON CONFLICT (district_code) DO NOTHING;
