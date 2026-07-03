-- Run in Supabase SQL editor after 002_mention_signals.sql.

CREATE TABLE IF NOT EXISTS mention_opportunities (
    market_id               TEXT PRIMARY KEY REFERENCES mention_markets(market_id),
    blended_confidence      NUMERIC(5, 4) NOT NULL,
    edge_pct                NUMERIC(5, 4) NOT NULL,
    implied_probability     NUMERIC(5, 4),
    best_ask                NUMERIC(6, 4),
    max_size_usd            NUMERIC(10, 2),
    total_depth_usd         NUMERIC(10, 2),
    liquidity_flag          BOOLEAN NOT NULL DEFAULT FALSE,
    status                  TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|skipped|expired
    alerted                 BOOLEAN NOT NULL DEFAULT FALSE,
    qualified_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_price_check_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mention_opp_pending
    ON mention_opportunities (status)
    WHERE status = 'pending';
