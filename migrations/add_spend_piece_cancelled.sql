-- A mail piece can be cancelled: it stays in the tracker but costs nothing (24 Oct 2026, Mail 8).
ALTER TABLE spend_piece DROP CONSTRAINT IF EXISTS spend_piece_status_ck;
ALTER TABLE spend_piece ADD CONSTRAINT spend_piece_status_ck
    CHECK (status IN ('draft', 'scheduled', 'delivered', 'cancelled'));
