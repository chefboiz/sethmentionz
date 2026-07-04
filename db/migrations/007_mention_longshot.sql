-- Run via: bash db/migrate.sh   (runs all migrations in order)

-- Volume/liquidity tracking on mention_markets (populated by scanner)
ALTER TABLE mention_markets
    ADD COLUMN IF NOT EXISTS volume24hr  NUMERIC(14, 2),
    ADD COLUMN IF NOT EXISTS liquidity   NUMERIC(14, 2);

-- Strategy column on mention_trades (default 'llm_confidence' for existing rows)
ALTER TABLE mention_trades
    ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT 'llm_confidence';

-- Price + volume history snapshots for momentum scoring
CREATE TABLE IF NOT EXISTS mention_price_history (
    id         BIGSERIAL PRIMARY KEY,
    market_id  TEXT NOT NULL REFERENCES mention_markets(market_id),
    yes_price  NUMERIC(6, 4),
    no_price   NUMERIC(6, 4),
    volume24hr NUMERIC(14, 2),
    snapped_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mention_price_history_market_time
    ON mention_price_history (market_id, snapped_at DESC);

-- Longshot candidates scored by momentum/volume/time composite
CREATE TABLE IF NOT EXISTS mention_longshot_candidates (
    market_id               TEXT PRIMARY KEY REFERENCES mention_markets(market_id),
    cheap_side              TEXT NOT NULL,     -- 'yes' | 'no'
    cheap_price             NUMERIC(6, 4),
    expensive_price         NUMERIC(6, 4),
    price_change_24h_pct    NUMERIC(8, 4),
    volume_24h              NUMERIC(14, 2),
    volume_7d_avg           NUMERIC(14, 2),
    hours_to_resolution     NUMERIC(8, 2),
    momentum_score          NUMERIC(5, 4),
    volume_score            NUMERIC(5, 4),
    time_score              NUMERIC(5, 4),
    composite_score         NUMERIC(5, 4),
    subject                 TEXT,
    last_scored_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_alerted_at         TIMESTAMPTZ,
    alerted_composite_score NUMERIC(5, 4)
);

CREATE INDEX IF NOT EXISTS idx_mention_longshot_score
    ON mention_longshot_candidates (composite_score DESC)
    WHERE composite_score IS NOT NULL;
