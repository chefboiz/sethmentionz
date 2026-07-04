-- Run via: sudo -u postgres psql sethmentionz -f db/migrations/008_mention_markets_volume.sql

ALTER TABLE mention_markets
    ADD COLUMN IF NOT EXISTS volume24hr NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS liquidity  NUMERIC(12, 2);
