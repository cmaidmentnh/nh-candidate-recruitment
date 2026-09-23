-- Streaming audio / podcast ads (StackAdapt audio), added 23 Sep 2026 for the CTEHR
-- affordability spots. Budgeted in dollars like meta/ctv/display: qty IS the budget.
-- rate is the planning CPM, used only to estimate impressions.
INSERT INTO spend_tactic (tactic_key, label, unit, rate, qty_label, grp, sort_order, active)
VALUES ('audio', 'Streaming audio / podcasts', 'dollars', 25.0000, 'dollars', 'digital', 45, true)
ON CONFLICT (tactic_key) DO NOTHING;
