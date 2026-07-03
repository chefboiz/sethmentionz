-- Run via: bash db/migrate.sh   (or: psql $DATABASE_URL -f db/migrations/001_mention_markets.sql)

CREATE TABLE IF NOT EXISTS mention_markets (
    market_id                   TEXT PRIMARY KEY,
    slug                        TEXT,
    question                    TEXT NOT NULL,
    description                 TEXT,
    resolution_source           TEXT,

    -- extracted by scanner/extractor.py
    subject                     TEXT,
    phrase_topic                TEXT,
    context                     TEXT,     -- speech|tweet|interview|debate|post|hearing|other
    resolution_criteria_summary TEXT,

    resolution_deadline         TIMESTAMPTZ,
    yes_price                   NUMERIC(6, 4),
    no_price                    NUMERIC(6, 4),
    clob_token_ids              JSONB,

    first_seen                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    resolved                    BOOLEAN NOT NULL DEFAULT FALSE,
    archived                    BOOLEAN NOT NULL DEFAULT FALSE
);

-- fast lookup for the active window
CREATE INDEX IF NOT EXISTS idx_mention_markets_active
    ON mention_markets (resolution_deadline)
    WHERE resolved = FALSE AND archived = FALSE;
