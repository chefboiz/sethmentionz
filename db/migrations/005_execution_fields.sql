-- Run via: bash db/migrate.sh   (runs all migrations in order)

ALTER TABLE mention_trades
    ADD COLUMN IF NOT EXISTS order_id        TEXT,
    ADD COLUMN IF NOT EXISTS limit_price     NUMERIC(6, 4),
    ADD COLUMN IF NOT EXISTS fill_price      NUMERIC(6, 4),
    ADD COLUMN IF NOT EXISTS fill_shares     NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS fill_size_usd   NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS fill_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancelled_at    TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_mention_trades_pending_fill
    ON mention_trades (order_id)
    WHERE status = 'approved';
