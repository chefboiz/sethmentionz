-- Run via: bash db/migrate.sh   (runs all migrations in order)

-- Re-alert tracking + message reference on the opportunity row
ALTER TABLE mention_opportunities
    ADD COLUMN IF NOT EXISTS alerted_edge_pct    NUMERIC(5, 4),
    ADD COLUMN IF NOT EXISTS alerted_confidence  NUMERIC(5, 4),
    ADD COLUMN IF NOT EXISTS tg_message_id       INTEGER;

-- Dry-run and live trade log
CREATE TABLE IF NOT EXISTS mention_trades (
    id                BIGSERIAL PRIMARY KEY,
    market_id         TEXT NOT NULL REFERENCES mention_markets(market_id),
    side              TEXT NOT NULL DEFAULT 'YES',
    size_usd          NUMERIC(10, 2) NOT NULL,
    price             NUMERIC(6, 4),
    confidence        NUMERIC(5, 4),
    edge_pct          NUMERIC(5, 4),
    status            TEXT NOT NULL,   -- approved_dry_run|approved|filled|partial|cancelled
    telegram_chat_id  TEXT,
    approved_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mention_trades_market
    ON mention_trades (market_id, approved_at DESC);
