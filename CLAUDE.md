# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

CodeSage is a Claude-powered multi-agent PR review assistant: a FastAPI backend (`app/`) running a LangGraph pipeline, a pgvector RAG store, an MCP server, and a React operator console (`web/`).

## Commands

Backend (use the local venv at `.venv`). Running the API needs `CODESAGE_API_KEYS=<key>` (or `CODESAGE_AUTH_DISABLED=true` for local-only dev), else every `/api/v1` route returns 503:

```bash
pip install -e ".[server,dev]"              # or: make dev (engine-only: pip install .)
.venv/bin/pytest -q                         # full suite (make test)
.venv/bin/pytest tests/test_review_graph.py::test_name -q   # single test
ruff check app tests scripts                # lint (make lint) — CI fails on this
ruff check --fix app tests scripts && ruff format app tests scripts   # make fmt
mypy app                                    # advisory only in CI (|| true)
uvicorn app.main:app --reload               # API on :8000, OpenAPI at /docs
python -m scripts.init_db                   # create pgvector extension + tables (non-Docker)
python -m scripts.ingest_repo --path /path/to/repo --repo owner/name   # RAG ingest
python -m app.eval.harness run --dataset eval/datasets/sample.jsonl
python -m app.eval.harness compare --baseline <model> --candidate <model>
python -m app.mcp.server                    # stdio MCP server
```

Frontend (`web/`): `npm run dev` (:5173, Vite proxies `/api` → :8000), `npm run build` (`tsc -b && vite build`), `npm run lint` (`tsc --noEmit`).

Full stack: `docker compose up --build` → web :3000 (nginx, proxies `/api` to api with buffering off for SSE), api :8000, db :5432. For local dev without Docker: `docker compose up -d db` and set `DATABASE_URL=postgresql+asyncpg://codesage:codesage@localhost:5432/codesage`.

## Stub mode (important for tests)

With no `ANTHROPIC_API_KEY`, `LLMClient` (`app/llm/client.py`) routes every call to `app/llm/stub.py`, which returns deterministic, regex-heuristic structured output keyed by component name (`security`, `correctness`, `style`, `reflection`, `judge`). `tests/conftest.py` forces stub mode and a dummy DB DSN before any app import, so **the suite must pass offline with no API key and no database**. Consequences:
- Any new LLM-calling component needs a matching branch in `stub.generate()`.
- Tests only exercise the stub path; the live path (LangChain `create_agent` + `ChatAnthropic`, see `app/llm/models.py`) is not covered by tests.
- Without Voyage (`VOYAGE_API_KEY`), embeddings fall back to a hash embedding (`app/rag/embeddings.py`).
- `Settings` is `lru_cache`d (`app/config.py`); env must be set before first `get_settings()` call.

## Architecture

**Review pipeline** (`app/agents/graph.py`): a compiled LangGraph `StateGraph` over `ReviewState` (`app/agents/state.py`):
`START → {security, correctness, style} (parallel) → reflection → judge → human_gate → END`.
- Parallel reviewers write to `draft_findings`/`traces`, which use `operator.add` reducers so concurrent writes merge. Other state keys are last-write.
- Reviewers subclass `ReviewerAgent` (`app/agents/reviewers/base.py`). The "ReAct" loop is a fixed sequence: retrieve RAG context → optionally run sandbox tests (`uses_sandbox`) → one `client.structured()` call. The `ReactTrace` steps are recorded for display; tool calls are not model-chosen (`TOOL_SCHEMAS` in `app/agents/tools.py` exist but are not passed to Claude).
- `human_gate` only sets `requires_human_approval` (judge score < `CODESAGE_HITL_THRESHOLD` or any high/critical finding). There is no LangGraph `interrupt()`/checkpointer; approval happens afterward via `POST /api/v1/reviews/{id}/approve`, which just updates the DB row.
- Dependencies are injected via `GraphDeps` / `make_deps()`. The graph is built in three places that must stay consistent: `ReviewService` (`app/services/review_service.py`), the MCP server (`app/mcp/server.py`), and the eval harness.

**Concurrency gotcha:** reviewers run concurrently, so `ReviewTools.retrieve_context` opens its own short-lived session from a session factory per call — never share one `AsyncSession` across graph nodes. RAG failures are swallowed and return `[]`.

**Two review execution paths** in `ReviewService`, both via `_execute_graph`:
- Sync: `POST /api/v1/reviews` → `run_review` (returns full result).
- Async/streaming: `POST /api/v1/reviews/async` (202) → `JobManager` (`app/services/job_manager.py`) runs `run_review_streamed` on a detached task with its own DB session, driving `graph.astream(stream_mode="updates")` and publishing `review.started` → `stage.completed`×6 → `telemetry.update` → `review.completed`/`review.failed` to `ReviewBroker` (`app/realtime/broker.py`), consumed by `GET /api/v1/reviews/{id}/stream` (SSE).
- The broker is in-process asyncio pub/sub with a replay buffer — only valid with a single Uvicorn worker.

**Stage names are duplicated across layers.** If you add/rename/reorder graph nodes, update `STAGE_ORDER` in `review_service.py` and the stage dependency/label maps in `web/src/lib/useReviewStream.ts` (the frontend derives running/pending states from that deps map since the stream only emits completions), plus `stub.py` and `StageTimeline.tsx` as needed.

**Security model:** `/api/v1` routes sit on `router` in `app/api/routes.py`, which carries the `require_api_key` dependency (`app/api/auth.py`, keys from `CODESAGE_API_KEYS`, fails closed with 503 when none are set). Only non-sensitive endpoints go on `public_router` (`/meta`). New routes belong on `router`; `tests/test_security.py` lists every protected route and must be extended. The web console sends the key from localStorage (`web/src/lib/auth.ts`) and reads SSE via `fetch` in `useReviewStream.ts` (not `EventSource`, which can't send headers). Sandbox is off by default and forced off in prod compose; API ingest is limited to `CODESAGE_INGEST_ROOTS`; API eval datasets to `eval/datasets/`. Logging goes to **stderr** — stdout belongs to the MCP stdio transport.

**Telemetry:** every `LLMClient` call records tokens/cost into a per-review `CostTracker` (`app/llm/telemetry.py`), persisted as `usage_events` rows and aggregated by `GET /api/v1/telemetry`.

**Eval:** `app/eval/judge.py` (LLM-as-Judge, also used as the `judge` graph node), `harness.py` (CLI; writes JSON results to `eval/results/`), `regression.py` (cross-model comparison). `app/eval/store.py` lists those files and launches runs as background tasks for the `/api/v1/eval/*` endpoints.

**Database schema has two sources of truth:** `migrations/0001_init.sql` (mounted into Postgres `docker-entrypoint-initdb.d`, runs on first boot only) and SQLAlchemy models in `app/db/models.py` (used by `scripts/init_db.py` `create_all`). Change both together. There is no migration tool; existing Docker volumes won't pick up SQL changes.

## Conventions

- Python ≥3.11, ruff line length 100, rules `E,F,I,UP,B` (B008 ignored for FastAPI `Depends`).
- `pytest` uses `asyncio_mode = "auto"` — write async tests as plain `async def`.
- Adding a reviewer: subclass `ReviewerAgent` (set `name`/`component`/`system`/`focus_query_template`), register it in `graph.py`, add a stub branch in `stub.py`, and update the stage maps above.
- Commit messages: conventional, present tense (`add`, `fix`, `refactor`).
