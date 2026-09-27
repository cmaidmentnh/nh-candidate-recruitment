-- 'appointed': a general election nominee named by the party after the primary (no primary win,
-- fewer than 35 write-ins). Applied 2026-09-27 by reconcile_filings_sos.py, which snapshotted
-- filings to filings_bak_20260927 first.
ALTER TABLE filings DROP CONSTRAINT IF EXISTS filings_result_check;
ALTER TABLE filings ADD CONSTRAINT filings_result_check
    CHECK (result IN ('pending', 'won', 'lost', 'withdrawn', 'appointed'));
