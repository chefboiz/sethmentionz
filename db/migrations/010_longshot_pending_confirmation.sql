-- Track whether a longshot candidate is mid-confirmation (b sent, y/n not yet received)
ALTER TABLE mention_longshot_candidates
    ADD COLUMN IF NOT EXISTS pending_confirmation_at TIMESTAMPTZ;

