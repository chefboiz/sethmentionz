-- Add LLM probability fields to longshot candidates
ALTER TABLE mention_longshot_candidates
    ADD COLUMN IF NOT EXISTS llm_probability NUMERIC(5, 4),
    ADD COLUMN IF NOT EXISTS llm_reasoning   TEXT;

