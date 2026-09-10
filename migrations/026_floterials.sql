-- 026_floterials.sql
-- Floterial districts overlay base districts. CTEHR does not mail a floterial separately:
-- one piece goes into the BASE district carrying the floterial candidate as well. Costing a
-- floterial's own households therefore bills the same household twice.
--
-- Classification is derived from our own districts table, not the voter file's
-- floterial_district column, which is stale. For each town+ward, list the districts covering
-- it; a district whose geographies are shared with TWO OR MORE other districts is a floterial,
-- because that is what a floterial is for. This reproduces the voter file's count exactly:
-- 39 floterials, 164 base districts.
CREATE TABLE IF NOT EXISTS district_relation (
    district_code varchar(50) PRIMARY KEY,
    kind          varchar(10) NOT NULL,       -- 'base' | 'floterial'
    spans         smallint NOT NULL DEFAULT 0 -- for a floterial, how many bases it covers
);

CREATE TABLE IF NOT EXISTS district_floterial_base (
    floterial varchar(50) NOT NULL,
    base      varchar(50) NOT NULL,
    PRIMARY KEY (floterial, base)
);
CREATE INDEX IF NOT EXISTS idx_dfb_base ON district_floterial_base (base);

TRUNCATE district_relation;
TRUNCATE district_floterial_base;

WITH g AS (SELECT full_district_code d, upper(trim(town))||'/'||COALESCE(ward,0) geo
           FROM districts WHERE full_district_code IS NOT NULL GROUP BY 1,2),
pairs AS (SELECT DISTINCT a.d, b.d AS partner FROM g a JOIN g b ON a.geo=b.geo AND a.d<>b.d),
cls AS (SELECT d, count(DISTINCT partner) p FROM pairs GROUP BY d)
INSERT INTO district_relation (district_code, kind, spans)
SELECT dd.d, CASE WHEN COALESCE(c.p,0) > 1 THEN 'floterial' ELSE 'base' END,
       CASE WHEN COALESCE(c.p,0) > 1 THEN c.p ELSE 0 END
FROM (SELECT DISTINCT d FROM g) dd LEFT JOIN cls c ON c.d = dd.d;

WITH g AS (SELECT full_district_code d, upper(trim(town))||'/'||COALESCE(ward,0) geo
           FROM districts WHERE full_district_code IS NOT NULL GROUP BY 1,2),
pairs AS (SELECT DISTINCT a.d, b.d AS partner FROM g a JOIN g b ON a.geo=b.geo AND a.d<>b.d)
INSERT INTO district_floterial_base (floterial, base)
SELECT p.d, p.partner FROM pairs p
JOIN district_relation r ON r.district_code = p.d AND r.kind = 'floterial';
