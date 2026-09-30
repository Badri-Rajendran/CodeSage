# CodeSage — Implementation Plan for the Agentic Workflows

## Context

The design is written and committed on `feat/agentic-workflows` (commit `b1f0580`):

- `docs/architecture.md`
- `docs/design/01-agent-engine.md`
- `docs/design/02-github-action.md`
- `docs/design/03-local-hitl-console.md`
- `docs/decisions.md` (D1–D19)
- `docs/roadmap.md`

It turns CodeSage into a personal PR reviewer shipped as a GitHub Action, with three kinds of agent:

- reviewers that really are agents (LangChain `create_agent` + tools)
- reflection that returns decisions
- an Opus 5 judge that can ask for one revision round

The review is posted back to GitHub. A local console runs a real `interrupt()` approval gate.

Phase 0 (baseline) is done. This plan turns roadmap Phases 1–7 into tasks. It doesn't reopen any approved design choice.

### Execution rules (from the owner, 2026-09-24)

- **Paid API runs:** ask before **every** run that calls the real Anthropic API, give an estimate, and report the actual cost afterwards.
- **Phase 5 GitHub work:** the owner installs and authenticates `gh`, and I drive it. Every outward-facing step needs its own confirmation: pushing the branch to the public repo, creating the test repo, opening PRs, posting comments, adding labels. The owner sets the `ANTHROPIC_API_KEY` repo secret themselves (`! gh secret set …`).
- **Existing tests:** update calls to match new interfaces only. Never remove or weaken an assertion, and list every test edit in the commit message.
- **Commits:** one conventional commit per task, made after `ruff check app tests scripts` and `.venv/bin/pytest -q` pass (and the web build when `web/` changed). Stay on `feat/agentic-workflows`. Nothing is pushed before Phase 5, and nothing is merged to `main` without the owner's approval.
- **No new automated tests** (D12). Each phase is verified with the manual checks listed.
- **Code review:** at the end of each phase, run a reviewer subagent on the phase's diff. Critical or Important findings are fixed before the next phase starts.
- **Environment:** Docker isn't running at the moment; the owner starts Docker Desktop before Phases 1 and 6. Port 5432 is free today, but throwaway databases still use 5544.

### Constraints found while exploring (they shape the tasks)

- **`tests/test_review_graph.py`:**
  - builds `build_review_graph(make_deps(client, tools, settings))` and uses `ainvoke` with no checkpointer, so the default mode must be `report` (no interrupt)
  - asserts `requires_human_approval`, and that `traces` agents are **exactly** `{security, correctness, style}`, so reflection and judge must not append to `traces`
  - asserts that every trace has both a `thought` and an `action` step, and that `reviewer == "security"` survives reflection
- **`tests/test_stub.py`** asserts the stub output shapes and ordering; `stub.py` must keep them.
- **`tests/test_security.py`:**
  - lists `/approve` in its protected-route parametrize (line ~91)
  - tests `Sandbox` timeout and process-group kill, so `app/agents/sandbox.py` stays and keeps passing
- **`tests/test_telemetry.py`** pins the existing prices; only add new rows.
- **The engine-only install is blocked** by `app/agents/tools.py` → sqlalchemy and `app/db/vector_store.py` → `models.py` → pgvector/numpy, with `RetrievedChunk` living in `vector_store.py`.
- **Stage names appear in five places:**
  - `STAGE_ORDER` (`app/services/review_service.py:37`)
  - `STAGE_DEPS`/`STAGE_LABEL` (`web/src/lib/useReviewStream.ts`)
  - `STAGES` (`web/src/lib/format.ts:3-21`, a copy CLAUDE.md doesn't mention)
  - `StageTimeline.tsx` `ICONS`
  - `StageName` (`web/src/lib/types.ts`)
- **Migrations:** `docker-compose.yml:12` mounts only `0001_init.sql`.
- **`tests/test_api.py`** uses `TestClient(app)` without `with`, so lifespan changes such as the checkpointer and reconciliation don't run in tests.

---

## Task 0 — Save this plan into the repo

Copy this plan to `docs/plans/2026-09-24-agentic-workflows.md`, link it from `docs/roadmap.md`, and commit.

## Phase 1 — Spike (throwaway, in the scratchpad, paid)

- **1.1** Make a separate venv in the scratchpad, not `.venv`, and install `langchain>=1.4.2,<1.5`, `langchain-anthropic>=1.7.4,<2`, `langgraph>=1.2.11,<1.3`, `langgraph-checkpoint-postgres`, `psycopg[binary]` and `psycopg-pool`. Record the versions that resolve.
- **1.2** `spike_agent.py`: runs `create_agent(ChatAnthropic("claude-sonnet-5", thinking adaptive, effort medium, cache_control), [read_file, search_code over this repo], response_format=ProviderStrategy(Findings))` on a planted `os.system(input())` diff. It checks four things:
  1. whether `structured_response` is valid
  2. whether `BudgetMiddleware` produces a structured answer after it strips the tools with `request.override(tools=[])` at a tiny allowance
  3. which `usage_metadata` fields exist, including `input_token_details.cache_read`/`cache_creation`
  4. how a refusal surfaces (inspect the `stop_reason` handling in the source; only trigger one live if that's cheap)

  It prints pass/fail and the cost for each check. **Ask before running it** (estimate < $0.50).
- **1.3** `spike_checkpoint.py`: a pgvector container on port 5544, then a two-node graph with `interrupt()` + `AsyncPostgresSaver`:
  - process A runs until the interrupt and exits
  - process B resumes with `Command(resume=…)`

  No API cost. The owner starts Docker first.
- **Gate:** report the results. If any item fails, **stop** and present the fallbacks from decisions.md (ToolStrategy + a `submit_findings` tool, or a separate final structured call). Record the findings in `docs/decisions.md` → "Open items" (commit).

## Phase 2 — Dependencies and packaging split

- **2.1** `pyproject.toml`:
  - Engine `dependencies`:
    - anthropic `>=0.120,<1`
    - langchain `>=1.4.2,<1.5`
    - langchain-anthropic `>=1.7.4,<2`
    - langgraph `>=1.2.11,<1.3`
    - langchain-core `>=1.6.4`
    - pydantic, pydantic-settings, httpx, pyyaml, tenacity
  - `[server]` extra: fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, pgvector, numpy, mcp, langgraph-checkpoint-postgres, psycopg[binary], psycopg-pool
  - `[dev]` stays.
  - `requirements.txt` becomes the engine set plus the server set, which is what Docker uses.
  - Update everywhere that installs `.[dev]` to install `.[server,dev]`: the Makefile `install`/`dev` targets, `ci.yml:22`, CONTRIBUTING.md:10 and the CLAUDE.md commands. Also update the Dockerfile.
  - Install into `.venv`.
- **2.2** Split the engine from the server:
  - Move `RetrievedChunk` to `app/rag/types.py`, and re-export it from `vector_store.py`.
  - In `app/agents/tools.py` and `reviewers/base.py`, move the sqlalchemy/`VectorStore` imports under `TYPE_CHECKING` or inside the function that uses them. These files are replaced in Phase 4 anyway; this keeps Phase 2 verifiable.
- **2.3** Re-check `app/llm/client.py` against anthropic ≥0.120: `messages.stream`, `output_config`, `stop_details` and the usage attribute names. Add the Claude 5 rows to `PRICES` (`app/llm/telemetry.py`). Optional: one live smoke call (ask first; < $0.02).
- **Verify:**
  - the standing checks pass
  - in a clean scratch venv, `pip install .` (engine only) then `python -c "import app.agents.graph, app.llm.client, app.github.client"` succeeds, and sqlalchemy, fastapi and pgvector don't appear in `sys.modules`

## Phase 3 — Diff model and Workspace

- **3.1** `app/diff/__init__.py`, `parser.py`:
  - `parse_diff(text) -> Diff`
  - `FileDiff(path, old_path, status, hunks, right_ranges, additions, deletions)`
  - `Diff.is_commentable(path, line)`, `Diff.filter(globs)`, `Diff.for_path(path)`, `Diff.render(path=None)`
  - Handles renames, `/dev/null`, binary and no-newline markers.
- **3.2** Move the process-group timeout and kill code (`sandbox.py:121-135`) into `app/workspace/proc.py::run_bounded(argv|shell, cwd, env, timeout, tail_chars)`. Have `Sandbox._exec_pytest` use it, and keep the "timeout" summary text so `test_security.py` still passes.
- **3.3** `app/workspace/workspace.py`: `Workspace(root, base_sha, head_sha, diff, test_cfg=None, semantic=None)` provides:
  - `read_file`, `search_code` (`git grep -n -I`), `list_dir` (`git ls-files`), `git_log`, `git_blame`, `get_diff`
  - path confinement with resolve + `is_relative_to`; `.git/` is refused
  - the per-tool caps and the 12,000-character cap on every result
  - `"error: …"` strings instead of exceptions
  - `tools_for(role, *, read_only=False) -> list[BaseTool]`, built with LangChain `StructuredTool`
- **3.4** `run_tests(target=None)`:
  - runs the setup command once, then the test command with `{target}` filled in
  - scrubbed environment (ANTHROPIC_API_KEY, GITHUB_TOKEN, `ACTIONS_*`, `CODESAGE_*`, and any name containing TOKEN, KEY or SECRET)
  - `run_bounded`; returns the exit code plus the last 4,000 characters of output
  - offered only to correctness, and only when `test_cfg.command` is set
- **3.5** `Workspace.clone_at(repo, head_sha, base_sha, token)`:
  - blobless clone, then fetch both SHAs and check out the head
  - the token goes in via `-c http.extraHeader`, never into `.git/config`
  - a context manager deletes the temp dir
- **3.6** `DiffOnlyWorkspace` for local diff mode, offering only `get_diff` plus `semantic_search`.
- **Verify:**
  - Parse `git diff 27bf9e6 6a4f2b1` and spot-check `right_ranges` against `git diff -U0`.
  - Run each tool on this repo, including a `../` escape and a symlink escape (both must be refused).
  - A `run_tests` command that sleeps past its timeout must be killed with the whole process group, and `env` inside `run_tests` must show no secrets.
  - `clone_at` on `Badri-Rajendran/CodeSage` at a known SHA (public, no token).

## Phase 4 — Review engine

- **4.1** `app/review_config.py`:
  - a Pydantic `ReviewConfig` with the models, effort, `budget_usd`, gate, `ignore_paths` (merged with the defaults), `max_files` and tests; extra keys are forbidden
  - `load_review_config(path | None, settings)`
  - `app/config.py` gains `reviewer_effort`, `judge_effort` and `checkpoint_dsn`, and the default models become `claude-sonnet-5` and `claude-opus-5` (D9)
- **4.2** `app/llm/models.py`: `build_chat_model(role, cfg) -> ChatAnthropic` with:
  - adaptive thinking
  - effort per role
  - `max_tokens` (reviewers 4,000; judge 3,000)
  - automatic `cache_control`
- **4.3** `app/llm/budget.py`:
  - `BudgetGuard` (total, allowances, reserve, `spent(agent)`, `remaining`, `can_start(agent)`), which also records into `CostTracker`
  - `BudgetMiddleware(agent, guard)`: `after_model` prices the call from `usage_metadata`; `wrap_model_call` strips the tools and adds the "finish now" note, using the Phase 1 recipe (or the chosen fallback)
- **4.4** `app/agents/middleware.py`: `TraceMiddleware` builds `ReactTrace` (`app/agents/react.py`, unchanged) from `after_model` and `wrap_tool_call`.
- **4.5** `app/agents/schemas.py`: `FindingDraft`, `Finding`, `Findings`, `Decision`, `ReflectionDecisions`, `JudgeResult`. In `app/llm/prompts.py`, rewrite the reviewer and reflection system prompts for tool use and remove `FINDINGS_SCHEMA` and `REFLECTION_SCHEMA`. `JUDGE_SYSTEM` and `JUDGE_SCHEMA` stay only if the stub path still uses them.
- **4.6** Rewrite `app/agents/reviewers/`:
  - `base.py` becomes `make_reviewer_node(role, deps)`. It builds `create_agent(model, workspace.tools_for(role), system_prompt, response_format=ProviderStrategy(Findings), middleware=[budget, ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"), trace])`, invokes it with a `recursion_limit`, reads `structured_response`, and assigns `id` and `reviewer`.
  - Stub path: `stub.generate(role, {"diff"})`, then assign ids and reviewer, then a scripted trace with one thought and one action (`get_diff`).
  - A refusal or a missing `structured_response` raises.
  - Keep the `security.py`/`correctness.py`/`style.py` role constants.
- **4.7** Rewrite `app/agents/reflection.py`:
  - a decision agent with read-only tools, `run_limit=4` and `ProviderStrategy(ReflectionDecisions)`
  - `apply_decisions(drafts, decisions, diff)`: pure code that handles keep, drop, merge (keeping `merged_from`) and field overrides, clears lines that aren't commentable, and sorts
  - Stub path: the existing `stub.generate("reflection")` dedup, whose output keeps `reviewer`
  - Not added to `traces`; the reflection trace goes in `reflection_trace`.
- **4.8** Rewrite `app/eval/judge.py`: `LLMJudge.score(...)` uses `build_chat_model("judge").with_structured_output(JudgeResult, method="json_schema")`, clamps the score, and records usage to the budget. The stub path is unchanged.
- **4.9** `app/agents/graph.py` and `state.py`:
  - New state keys: `pr`, `budget_limited`, `revision_count`, `gate_tripped`, `gate_reasons`, `decision`, `decision_note`, `github_review_url`, `reflection_trace`. Keep `requires_human_approval`, set to `gate_tripped` in report and action modes.
  - `EngineDeps(settings, cfg, workspace, diff, budget, tracker, publisher=None)` plus `make_deps(...)`.
  - `build_review_graph(deps, *, mode="report", checkpointer=None)` adds:
    - the `revise` conditional edge (score < threshold, `revision_count == 0`, remaining ≥ $0.08)
    - `compute_gate_reasons` (pure)
    - the `human_gate` modes (a real `interrupt()` in local mode, wired up in Phase 6)
    - a `publish` node that calls `deps.publisher` if one is set
  - If the reserve can't pay for reflection or the judge, that node is skipped and `budget_limited` is set.
- **4.10** Update the callers:
  - `review_service._execute_graph` (still report mode until Phase 6), `mcp/server.py`, `eval/harness.py` and `tests/test_review_graph.py` switch to the new `make_deps`; that test edit changes calls only.
  - Delete `ReviewTools` and `TOOL_SCHEMAS` from `app/agents/tools.py`; `semantic_search` becomes a Workspace tool that takes a session factory.
  - Stage sync: add `revise` and `publish` to all five stage locations. `_stage_event` handles them, and the UI shows `revise` as skipped when `human_gate` completes without it.
- **Verify:**
  - The standing checks pass, plus the web build.
  - A dev script in the scratchpad runs the engine on a CodeSage checkout with a planted diff. **Each run needs approval first** (about $0.30–0.50 each). It must show:
    1. correct `reviewer`, `path` and `line` values, real tool calls in the traces, and a total ≤ $0.50
    2. with `budget_usd: 0.02`, `budget_limited` is set and findings are still structured
    3. a deliberately weak review gets exactly one `revise` round
  - Stub mode offline still works through the MCP server and the eval harness (`python -m app.eval.harness run --dataset eval/datasets/sample.jsonl`).

## Phase 5 — GitHub Action

- **5.1** Add to `app/github/client.py`, taking a token argument and passing every repo name through `validate_repo`:
  - `get_pr`
  - `list_reviews`
  - `create_review` (`line`/`side`/`start_line`)
  - `create_check_run`
  - `update_check_run` (annotations in batches of 50)
  - `remove_label`
- **5.2** `app/publish/markdown.py`:
  - the review body, with the header, severity counts, findings that couldn't go inline, the summary, the cost table, the budget note, the version footer and the `<!-- codesage-review:{id} -->` marker
  - the inline comment body
  - the check title
  - summaries truncated to 65,535 characters and step summaries to 1 MiB

  `app/publish/github.py` `GitHubPublisher`:
  - `start_check`
  - `post_review`, with at most 30 inline comments and one retry that puts everything in the body if GitHub returns 422
  - `finish_check`, which chooses the conclusion in the order error > failure > neutral-only > success
- **5.3** `app/action/`:
  - `rules.py` (the trigger table and the fork skip)
  - `context.py` (event → `PRContext`; a comment event triggers a `get_pr` call)
  - `__main__.py` with the `resolve` and `review` subcommands, `--event`/`--workspace` for running locally, writes to `GITHUB_OUTPUT` and `GITHUB_STEP_SUMMARY`, and the exit codes from design 02
- **5.4** Add `action.yml` at the repo root (composite, as in design 02). Add `examples/codesage.yml` (the workflow) and `examples/.codesage.yml`. Add a Usage section to the README, including the note that comment triggers need the workflow file on the default branch.
- **5.5** Local dry run: `python -m app.action review --event <saved payload> --workspace .`, pointed at a GitHub stub (a `CODESAGE_DRY_RUN` flag that prints the API calls instead of sending them).
  - Stub mode: free.
  - Live: ask first.
- **5.6** Live verification with `gh`. **Each step needs the owner's confirmation first:**
  1. Push `feat/agentic-workflows`.
  2. Create the private test repo, containing the workflow pointed at `@feat/agentic-workflows`. The owner sets the secret.
  3. Run these cases, **asking before each paid run**:
     - a PR with a planted bug
     - a clean PR
     - a draft PR, which is skipped, then marked ready
     - `/codesage review` from the owner
     - the label trigger, after which the label is removed
     - the non-collaborator comment case, which can only be confirmed if a second account is available; otherwise it's checked with a saved payload
  4. Settle the four undocumented behaviours in decisions.md:
     - a comment on a line outside the diff
     - the maximum number of comments per review
     - the permission needed to remove a label
     - setup-python's current major version, then pin the action versions

  Update the docs.

## Phase 6 — Local HITL and console

- **6.1** Database:
  - `migrations/0002_agentic.sql` (the columns in design 03, `ADD COLUMN IF NOT EXISTS`)
  - update the `Review` model in `app/db/models.py`
  - `scripts/migrate.py`
  - `docker-compose.yml` mounts `./migrations` as a directory
- **6.2** Lifespan in `app/main.py`:
  - open `AsyncPostgresSaver` (`checkpoint_dsn`) and run `setup()`
  - reconcile rows stuck in `running`: mark them `failed` with `error='interrupted by restart'`
  - close everything on shutdown
- **6.3** Local mode in the engine:
  - `human_gate` calls `interrupt()` only in local mode
  - `LocalPublisher` wraps `GitHubPublisher.post_review` (review only). It posts when there's a PR and either the gate passed or the reviewer approved, first checking for the idempotency marker with `list_reviews`
  - PR mode uses `clone_at`; diff mode uses `DiffOnlyWorkspace` and publishes nothing
- **6.4** `ReviewService`, `JobManager` and `ReviewBroker`:
  - **Graph run:**
    - `thread_id` = the review id
    - an `__interrupt__` in the stream sets `awaiting_approval` and emits a `review.awaiting_approval` event
    - `_merge_delta` and `_stage_event` ignore `__interrupt__` and add the new additive keys
  - **Persistence:**
    - write every new column; `mark_failed` writes to `error`, not `summary`
    - `_review_to_dict` and `ReviewResponse` expose the new fields
    - `requires_human_approval` and `approved` become computed fields
  - **Decisions and resume:**
    - `decide(id, approved, note)` returns 409 unless the review is awaiting approval
    - `JobManager.submit_resume` rebuilds the graph and resumes it with `Command(resume=…)`
  - **Broker:** evicts a review's buffer 10 minutes after its final or awaiting event
- **6.5** Routes:
  - `POST /reviews/{id}/decision` replaces `/approve`; update the route in `test_security.py` (a call-only edit)
  - an id that isn't a valid UUID returns 404
  - SSE returns 404 for an unknown id; for a finished or awaiting review it sends the final event from the database, then closes
- **6.6** Console, following the design 03 table:
  - `types.ts`, `api.ts` (`decide`), `useReviewStream.ts` (the awaiting status and the skipped stage)
  - `format.ts`, `StageTimeline.tsx`
  - `ReviewConsole.tsx`, `ReviewDetail.tsx`, `ApprovalQueue.tsx`, `ReviewRow.tsx`, `History.tsx`
- **6.7** MCP runs in `report` mode. `review_github_pr` uses a `clone_at` workspace, and responses include `gate_reasons`.
- **Verify** on a local stack: pgvector on 5544, the API on a spare port, the console on 5174. Every paid run needs approval first; PR-mode runs post to the test repo from Phase 5.
  - A gate-tripping PR review reaches `awaiting_approval` and survives an API restart.
  - Approving posts once, and a repeated resume doesn't post twice.
  - Rejecting posts nothing.
  - A passing review posts automatically.
  - A review in diff mode completes without posting.
  - Review Detail shows traces, the judge's dimension scores and cost.
  - A row forced into `running` becomes `failed` on restart.
  - SSE returns 404 for an unknown id, and for a finished review it sends the final state and closes.
  - Check the browser with Playwright, then delete the screenshots.

## Phase 7 — Clean-up and docs

- **7.1** Delete `deploy/` and remove its references:
  - `README.md` lines 44, 265, 296, 301 and 307, plus the repo links at lines 7 and 90, which change to `Badri-Rajendran`
  - `CLAUDE.md:70`
  - `CONTRIBUTING.md:37`
  - `.env.example:34`
  - the docstring at `app/config.py:4`
  - `.dockerignore`
  - the stale `doctl` entry in `.claude/settings.local.json`, if the owner agrees
- **7.2** Rewrite the README around the Action, with the local console as a companion. Update CLAUDE.md with the new architecture, the commands (`.[server,dev]`, `scripts.migrate`, `app.action`), the five places stage names live, and the graph modes. Refresh `docs/workflow.svg`/`.png`. Mark the docs as implemented.
- **Verify:**
  - `grep -rniE "deploy/|digitalocean|badrinarayanan/CodeSage" --exclude-dir=.git .` finds only history in the docs
  - the standing checks pass
  - a final reviewer subagent pass over `main..feat/agentic-workflows`
  - the owner decides on the merge or PR

## Critical files

- **New:**
  - `app/diff/`, `app/workspace/`
  - `app/review_config.py`, `app/llm/models.py`, `app/llm/budget.py`
  - `app/agents/middleware.py`, `app/agents/schemas.py`
  - `app/publish/`, `app/action/`, `action.yml`
  - `migrations/0002_agentic.sql`, `scripts/migrate.py`
- **Rewritten:** `app/agents/{graph,state,reflection}.py`, `app/agents/reviewers/*`, `app/eval/judge.py`, `app/llm/prompts.py`
- **Extended:**
  - `app/github/client.py`, `app/services/{review_service,job_manager}.py`, `app/realtime/broker.py`
  - `app/api/{routes,schemas}.py`, `app/main.py`, `app/db/models.py`, `app/config.py`, `app/llm/telemetry.py`
  - `web/src/{lib,components,pages}`
- **Reused:**
  - `ReactTrace` (`app/agents/react.py`)
  - `CostTracker`/`cost_usd` (`app/llm/telemetry.py`)
  - `validate_repo` (`app/github/client.py`)
  - `VectorStore.search` (`app/db/vector_store.py`)
  - `resolve_ingest_root` symlink logic (`app/rag/pipeline.py`)
  - the process-group kill (`app/agents/sandbox.py`, moved to `app/workspace/proc.py`)
  - `require_api_key` (`app/api/auth.py`)
  - `stub.generate` (`app/llm/stub.py`)

## Verification summary

- Every task passes ruff and pytest (70 tests, stub mode, offline) before its commit, plus the web build when `web/` changed.
- Every phase ends with its manual checks from `docs/roadmap.md` (listed above) and a reviewer-subagent pass.
- Paid runs and outward-facing GitHub actions happen only after the owner confirms each one.
