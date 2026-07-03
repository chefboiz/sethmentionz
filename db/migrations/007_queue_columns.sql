-- Run via: bash db/migrate.sh   (runs all migrations in order)

ALTER TABLE mention_opportunities
    ADD COLUMN IF NOT EXISTS queued_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS queue_status TEXT;   -- 'queued' | 'open' | NULL

CREATE INDEX IF NOT EXISTS idx_mention_opp_queue
    ON mention_opportunities (queued_at)
    WHERE queue_status IS NOT NULL;
