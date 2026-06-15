-- CodeSage initial schema. Loaded automatically by the pgvector Postgres image
-- on first boot (docker-entrypoint-initdb.d). The app also creates these via
-- scripts/init_db.py for non-Docker setups.

CREATE EXTENSION IF NOT EXISTS vector;

-- Code chunks for the RAG pipeline. Embedding dim defaults to 1024 (voyage-3 /
-- the local hash fallback). If you change CODESAGE_EMBED_DIM, recreate this table.
CREATE TABLE IF NOT EXISTS code_chunks (
    id          BIGSERIAL PRIMARY KEY,
    repo        TEXT NOT NULL,
    path        TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    language    TEXT,
    content     TEXT NOT NULL,
    embedding   vector(1024),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_code_chunks_repo ON code_chunks (repo);

-- Approximate nearest-neighbour index for cosine distance.
CREATE INDEX IF NOT EXISTS idx_code_chunks_embedding
    ON code_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Persisted reviews.
CREATE TABLE IF NOT EXISTS reviews (
    id                       UUID PRIMARY KEY,
    repo                     TEXT NOT NULL,
    pr_number                INTEGER,
    status                   TEXT NOT NULL DEFAULT 'completed',
    model                    TEXT NOT NULL,
    findings                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary                  TEXT,
    judge_score              DOUBLE PRECISION,
    judge_rationale          TEXT,
    requires_human_approval  BOOLEAN NOT NULL DEFAULT false,
    approved                 BOOLEAN,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reviews_repo_pr ON reviews (repo, pr_number);

-- Token-cost telemetry, one row per model call.
CREATE TABLE IF NOT EXISTS usage_events (
    id                   BIGSERIAL PRIMARY KEY,
    review_id            UUID,
    component            TEXT NOT NULL,
    model                TEXT NOT NULL,
    input_tokens         INTEGER NOT NULL DEFAULT 0,
    output_tokens        INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd             DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_usage_review ON usage_events (review_id);

-- Eval runs, for cross-version regression detection.
CREATE TABLE IF NOT EXISTS eval_runs (
    id           UUID PRIMARY KEY,
    dataset      TEXT NOT NULL,
    model        TEXT NOT NULL,
    mean_score   DOUBLE PRECISION NOT NULL,
    n_cases      INTEGER NOT NULL,
    per_case     JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_model ON eval_runs (model);
