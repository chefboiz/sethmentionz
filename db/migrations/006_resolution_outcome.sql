-- Run in Supabase SQL editor after 005_execution_fields.sql.

ALTER TABLE mention_markets
    ADD COLUMN IF NOT EXISTS resolution_outcome TEXT;   -- 'YES' | 'NO'

ALTER TABLE mention_trades
    ADD COLUMN IF NOT EXISTS actual_outcome TEXT,       -- 'YES' | 'NO'
    ADD COLUMN IF NOT EXISTS won            BOOLEAN,
    ADD COLUMN IF NOT EXISTS resolved_at    TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_mention_trades_unresolved
    ON mention_trades (market_id)
    WHERE won IS NULL AND status IN ('filled', 'approved_dry_run', 'approved');
