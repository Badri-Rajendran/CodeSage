# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

CodeSage is a Claude-powered PR reviewer. It has two parts:
- **The main product:** a reusable composite **GitHub Action** (`action.yml` → the `codesage-action` console script).
- **A local companion:** a FastAPI backend (`app/`), a React operator console (`web/`), an MCP server and a pgvector RAG store, with a real human approval gate.

Both run the same LangGraph review engine. The design docs are in `docs/`: `architecture.md`, `design/01-03`, `decisions.md` (the decision log plus verified facts) and `roadmap.md`.

## Commands

Backend (use the local venv at `.venv`). Running the API needs `CODESAGE_API_KEYS=<key>` (or `CODESAGE_AUTH_DISABLED=true` for local-only dev); otherwise every `/api/v1` route returns 503:

```bash
pip install -e ".[server,dev]"              # or: make dev (engine only, as the Action installs it: pip install .)
.venv/bin/pytest -q                         # full suite (make test)
.venv/bin/pytest tests/test_review_graph.py::test_name -q   # single test
ruff check app tests scripts                # lint (make lint) — CI fails on this
ruff check --fix app tests scripts && ruff format app tests scripts   # make fmt
mypy app                                    # advisory only in CI (|| true)
uvicorn app.main:app --reload               # API on :8000, OpenAPI at /docs
python -m scripts.migrate                   # apply migrations/*.sql in order (idempotent)
python -m scripts.init_db                   # alternative: pgvector extension + ORM create_all (fresh DB)
python -m scripts.ingest_repo --path /path/to/repo --repo owner/name   # RAG ingest
python -m app.eval.harness run --dataset eval/datasets/sample.jsonl
python -m app.mcp.server                    # stdio MCP server
codesage-action review --event e.json --event-name pull_request --repository o/r \
  --workspace /path/to/checkout --dry-run --local-diff   # reproduce an Action run, post nothing
```

Frontend (`web/`): `npm run dev` (:5173; Vite proxies `/api` to `VITE_API_TARGET`, default :8000), `npm run build` (`tsc -b && vite build`), `npm run lint` (`tsc --noEmit`).

Full stack: `docker compose up --build` gives web :3000 (nginx, which proxies `/api` with SSE buffering off), api :8000 and db :5432. The compose file mounts `migrations/` into `docker-entrypoint-initdb.d`, so a fresh volume runs every migration; for an existing volume run `python -m scripts.migrate`.

## Stub mode (important for tests)

With no `ANTHROPIC_API_KEY`, `LLMClient.stubbed` (`app/llm/client.py`) is true. Reviewer, reflection and judge nodes then call `app/llm/stub.py` (deterministic regex heuristics keyed by `security`, `correctness`, `style`, `reflection` and `judge`) instead of Claude, and they emit a scripted trace.

`tests/conftest.py` forces stub mode and a dummy DB DSN before any app import, so **the suite must pass offline with no API key and no database**. Consequences:
- Any new LLM-calling node needs a stub branch.
- Tests only exercise the stub path. The live path isn't covered by tests: LangChain `create_agent` with `ChatAnthropic` from `app/llm/models.py`, `ProviderStrategy` structured output, and the middleware.
- Without Voyage (`VOYAGE_API_KEY`), embeddings fall back to a hash embedding (`app/rag/embeddings.py`).
- `Settings` is `lru_cache`d (`app/config.py`), so env must be set before the first `get_settings()` call. The Action builds `Settings(_env_file=None)` and passes it explicitly, because its working directory is the reviewed repo.

## Architecture

**Review graph** (`app/agents/graph.py`): a LangGraph `StateGraph` over `ReviewState` (`app/agents/state.py`):
`START → {security, correctness, style} (parallel) → reflection → judge → [revise → judge, at most once] → human_gate → publish → END`.

- **Reviewers** (`app/agents/reviewers/base.py`):
  - Each is a `create_agent` loop over workspace tools, with `response_format=ProviderStrategy(Findings)`.
  - Code, not the model, assigns each finding's `id` and `reviewer` (`app/agents/findings.py`).
  - Middleware (`app/agents/middleware.py`):
    - `FinishMiddleware` meters cost into `BudgetGuard` (`app/llm/budget.py`). Near the budget or at the last allowed call, it removes the tools so the model must return structured findings; jumping to `end` would return none.
    - `TraceMiddleware` records the real tool calls as a `ReactTrace`.
- **Reflection** (`app/agents/reflection.py`):
  - Returns keep/drop/merge *decisions*; `apply_decisions()` applies them in code, so attribution survives.
  - Line numbers are cleared unless they fall on commentable diff lines.
  - The same node, given the judge's feedback, is `revise`.
- **Judge:** `app/eval/judge.py`, native `json_schema` structured output.
- **`human_gate`:** computes `gate_reasons`. Its behaviour depends on the graph mode:
  - `report` (default: tests, MCP, eval) and `action`: never pause.
  - `local`: `interrupt()`, which needs a checkpointer.
- **`publish`:** calls `deps.publisher` if one is set.
- **State reducers:** the parallel reviewers write `draft_findings` and `traces` (`operator.add`) and `budget_limited` (`operator.or_`). `ReviewService._merge_delta` mirrors these using `ADDITIVE_KEYS`/`OR_KEYS` from `state.py`. Only the three reviewers may append to `traces`; tests assert this, and reflection's trace goes in `reflection_trace`.
- **Dependencies:** `make_deps(client, workspace, settings, cfg=, publisher=)` builds `EngineDeps` (`app/agents/deps.py`). `ReviewConfig` (`app/review_config.py`) merges a repo's `.codesage.yml` over env settings; `prepare_diff()` applies the ignore globs and `max_files`.
- **Where graphs are built:** `app/action/__main__.py`, `ReviewService`, `app/mcp/server.py` and `app/eval/harness.py`.

**Workspace and diff** (`app/workspace/`, `app/diff/`):
- **Tools:** `read_file`, `search_code` (`git grep`), `list_dir`, `git_log`, `git_blame`, `get_diff`, `run_tests` and `semantic_search`. They are LangChain tools, and errors come back as `"error: …"` strings, not exceptions.
  - Paths are resolved, symlinks included, and must stay inside the root.
  - `.git` is refused, case-insensitively.
  - Output is capped at 12,000 chars.
- **`run_tests`:** runs only when `.codesage.yml` sets `tests.command`, and is offered only to the correctness reviewer. It gets a scrubbed environment (no tokens or keys) and a process-group timeout (`app/workspace/proc.py`).
- **Three workspace shapes:**
  - `Workspace(root, diff)`: the Action's checkout.
  - `Workspace.clone_at(...)`: local PR mode, a blobless temporary clone; the token is passed via `GIT_CONFIG_*` env, never written to `.git/config`.
  - `Workspace.diff_only(...)`: local diff mode.
- **`Diff.is_commentable(path, line)`:** true for added or context lines on the RIGHT side. These are the only lines GitHub accepts for inline comments.

**GitHub Action** (`action.yml`, `app/action/`, `app/publish/`):
- **Steps:** setup-python@v7 → `pip install` the engine into a venv → `codesage-action resolve` → checkout@v7 at the head SHA (full history, `persist-credentials: false`) → `codesage-action review`.
  - It is a console script, **not** `python -m app.action`: `-m` would put the reviewed repo first on `sys.path`, so a repo with its own `app/` package would be imported.
- **Triggers** (`app/action/rules.py`, mirrored in `examples/codesage-workflow.yml`):
  - PR opened (not a draft) or marked ready for review
  - the `codesage:review` label, which is removed afterwards
  - a `/codesage review` comment from OWNER/MEMBER/COLLABORATOR

  Forks are skipped.
- **Publishing:** `GitHubPublisher` posts one `COMMENT` review with up to 30 inline comments (retrying body-only on a 422) and the `CodeSage` check run. The check conclusion is error > failure > neutral (budget-limited only) > success.
- **Exit codes:** 0 when the review is published, whether the gate passed or tripped; 1 on an engine or publishing error. The check run is always completed.

**Local mode** (`app/services/`, `app/api/`, `app/main.py`):
- **Lifespan:** opens LangGraph's `AsyncPostgresSaver` (psycopg v3, using `settings.checkpoint_dsn`, which is `DATABASE_URL` without `+asyncpg`) and marks rows stuck in `running` as `failed`. Without a checkpointer, reviews run in `report` mode and the gate can't pause.
- **`POST /reviews/async`:** `JobManager` runs `ReviewService.run_review_streamed` with `thread_id` = review id. A tripped gate interrupts the run and sets status `awaiting_approval`. PR mode means a PR number, no diff, and `GITHUB_TOKEN` set: the agents review a clone at the head, and the review can be posted.
- **`POST /reviews/{id}/decision`:** the `awaiting_approval → running` transition is atomic (409 otherwise). The review then resumes with `Command(resume=…)`.
  - Approve posts to the PR. Posting is idempotent: a marker `<!-- codesage-review:{id} -->` is checked via `list_reviews`.
  - Reject posts nothing.
  - A passing gate posts automatically.
- **Statuses:** `running`, `awaiting_approval`, `completed`, `rejected`, `failed`.
- **SSE** (`GET /reviews/{id}/stream`, backed by the in-process `ReviewBroker`, so a single Uvicorn worker only):
  - Unknown or malformed ids return 404.
  - Streams end on completed, failed or awaiting.
  - A finished or paused review that's no longer buffered gets its final event rebuilt from the DB.
  - `broker.reopen()` lets a resumed review continue the same stream, and buffers are evicted 10 minutes after the end.
- **Concurrency:** the reviewers run in parallel, so `semantic_search` (`app/rag/search.py`) opens its own session per call. Never share one `AsyncSession` across graph nodes.

**Stage names live in five places.** If you add, rename or reorder graph nodes, update all of them:
- `STAGES` in `app/agents/graph.py`; `ReviewService.STAGE_ORDER` derives from it
- `STAGES` in `web/src/lib/format.ts`
- `STAGE_DEPS`/`STAGE_LABEL`/`OPTIONAL_STAGES` in `web/src/lib/useReviewStream.ts`
- `StageName` in `web/src/lib/types.ts`
- `ICONS` in `web/src/components/StageTimeline.tsx`

The stream only reports completions, so the console derives running, pending, skipped and awaiting from the dependency map.

**Security model:**
- **Auth:** `/api/v1` routes sit on `router` (`app/api/routes.py`) with `require_api_key` (`app/api/auth.py`, keys from `CODESAGE_API_KEYS`; it fails closed with 503). Only `/meta` is on `public_router`. New routes belong on `router`, and `tests/test_security.py` lists every protected route.
- **Console:** sends the key from localStorage and reads SSE via `fetch`.
- **Other limits:** API ingest is limited to `CODESAGE_INGEST_ROOTS`, and API eval datasets to `eval/datasets/`. Repo names go through `validate_repo` before any token-bearing call.
- **Logging:** goes to **stderr**, because stdout belongs to the MCP stdio transport.

**Telemetry:** `BudgetGuard.record` prices each call from LangChain `usage_metadata`. Here `input_tokens` includes cached tokens, and cache writes appear either as `cache_creation` or as the `ephemeral_5m`/`ephemeral_1h` keys. It records into the review's `CostTracker` (`app/llm/telemetry.py`), which is persisted as `usage_events` rows plus the review's `telemetry` column.

**Database schema has two sources of truth:** `migrations/*.sql` (idempotent, applied by `scripts/migrate.py` or on first boot) and the SQLAlchemy models in `app/db/models.py`. Change both together.

## Conventions

- Python ≥3.11, ruff line length 100, rules `E,F,I,UP,B` (B008 ignored for FastAPI `Depends`).
- `pytest` uses `asyncio_mode = "auto"`, so write async tests as plain `async def`.
- Adding a reviewer: see "Adding a reviewer" in `CONTRIBUTING.md`.
- Engine modules (everything the Action imports) must not import server-only packages (fastapi, sqlalchemy, pgvector, mcp, psycopg) at module level; import them lazily or under `TYPE_CHECKING`.
- Commit messages are conventional and present tense (`add`, `fix`, `refactor`). **Never add `Co-Authored-By` or other Claude/session attribution trailers.**
