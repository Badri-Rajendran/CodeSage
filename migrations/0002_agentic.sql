-- Agentic workflows: real approval gate, persisted traces/telemetry, PR context,
-- GitHub posting. Idempotent: safe to run again (python -m scripts.migrate).

ALTER TABLE reviews ADD COLUMN IF NOT EXISTS traces            JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS judge_dimensions  JSONB;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS telemetry         JSONB;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS base_sha          TEXT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS head_sha          TEXT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS pr_title          TEXT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS gate_tripped      BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS gate_reasons      JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS budget_limited    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS revision_count    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS decision          TEXT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS decision_note     TEXT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS decided_at        TIMESTAMPTZ;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS github_review_url TEXT;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS error             TEXT;

CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews (status);
