-- Run via: bash db/migrate.sh   (runs all migrations in order)

CREATE TABLE IF NOT EXISTS mention_signals (
    id                      BIGSERIAL PRIMARY KEY,
    market_id               TEXT NOT NULL REFERENCES mention_markets(market_id),

    llm_score               NUMERIC(5, 4),
    signal_score            NUMERIC(5, 4),
    blended_score           NUMERIC(5, 4),

    llm_weight              NUMERIC(4, 3),
    signal_weight           NUMERIC(4, 3),

    llm_reasoning           TEXT,
    llm_context_confidence  TEXT,   -- low|medium|high

    scored_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- latest score per market — used by edge calculator
CREATE INDEX IF NOT EXISTS idx_mention_signals_market_latest
    ON mention_signals (market_id, scored_at DESC);
