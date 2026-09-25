# Roadmap

Build order for the approved design in [architecture.md](architecture.md). Each phase
ends with the verification listed for it, and nothing moves to the next phase while
the current one fails. The task-level implementation plan, including the owner's
execution rules (ask before each paid API run and each outward-facing GitHub step),
is [plans/2026-09-24-agentic-workflows.md](plans/2026-09-24-agentic-workflows.md).

**Branch:** `feat/agentic-workflows`. **Status:** all phases done; verification logs are in
[decisions.md](decisions.md). The branch hasn't been merged to `main`; that is the owner's call.

## Standing checks (every phase)

- `ruff check app tests scripts` is clean.
- `.venv/bin/pytest -q`: all existing tests pass (70 at baseline `51be21f`). They
  run in stub mode, so no API key or database is needed.
- `cd web && npx tsc -b && npx vite build` passes whenever `web/` changed.
- No new automated tests in this round, by the owner's decision (D12). Anything
  new is checked with the manual steps below.

## Phases

### Phase 0: Baseline ✅ done

- Branch `feat/agentic-workflows` created.
- The API lockdown and CLAUDE.md are committed (`51be21f`).
- `origin` now points at `https://github.com/Badri-Rajendran/CodeSage.git`.

### Phase 1: Spike (throwaway) ✅ done

This proves the five open items in [decisions.md](decisions.md#open-items-for-the-phase-1-spike)
against the live API. The spike code lives in the session scratchpad, **not** in the repo.

**Verify:**
- A script prints pass/fail for each item, plus the real cost.
- The Postgres checks use a throwaway pgvector container on a free port, because
  5432 is taken by another project on the owner's machine.
- **If an item fails, stop** and present the fallback options to the owner before
  continuing.

### Phase 2: Dependencies and packaging split ✅ done

**Changes:**
- **Engine dependencies** in `pyproject.toml` / `requirements.txt`:
  - `langchain>=1.4.2,<1.5`
  - `langchain-anthropic>=1.7.4,<2`
  - `anthropic>=0.120,<1`
  - `langgraph>=1.2.11,<1.3`
  - `pyyaml`
- **`[server]` extra:** the FastAPI, DB and MCP stack, plus `langgraph-checkpoint-postgres`,
  `psycopg[binary]` and `psycopg-pool`.
- Makefile, Dockerfile, CI and CONTRIBUTING are updated to install `.[server,dev]`.
- `app/llm/client.py` is re-checked against the upgraded SDK.

**Verify:**
- The standing checks pass.
- `pip install .` (engine only) into a clean venv imports `app.action` without any
  server package.

### Phase 3: Diff model and Workspace ✅ done

**Changes:**
- `app/diff/`: parser, `is_commentable`, `filter`.
- `app/workspace/`: tools, path confinement, output caps, `clone_at`, and
  `run_tests` with a scrubbed environment and process-group timeout.

**Verify:**
- Parse real diffs from this repo's history (`git diff 27bf9e6 6a4f2b1`) and spot-check
  the hunk ranges against `git diff -U0`.
- Run each tool against this repo, including a path that escapes the root (must be
  refused) and a `run_tests` command that sleeps past its timeout (must be killed).

### Phase 4: Review engine ✅ done

**Changes:**
- `app/llm/models.py`
- `app/llm/budget.py`
- `PRICES` update
- middleware (budget, trace, call limits)
- reviewer agents
- decision-based reflection and `apply_decisions`
- the Opus 5 judge
- the `revise` edge
- graph modes (`action`, `local`, `report`)
- stub path
- rewritten prompts

**Verify:**
- The standing checks pass (stub mode).
- A manual run on a CodeSage checkout with a planted `os.system(input())` diff:
  - the findings have the correct `reviewer`, `path` and `line`
  - the traces show real tool calls
  - the total cost is ≤ $0.50
- The same run with `budget_usd: 0.02` reports `budget_limited` and still returns structured findings.
- A low-quality planted review is judged below 0.6 and gets exactly one `revise` round.

### Phase 5: GitHub Action ✅ done

**Changes:**
- `app/action/`: `resolve` and `review` commands, rules, config, the local reproduction entry point.
- `action.yml`
- `app/publish/github.py`: review, inline comments, check run, markdown, job summary.
- `GitHubClient` additions.
- README usage section with an example workflow and `.codesage.yml`.

**Verify** on a private test repo owned by the owner, using `@feat/agentic-workflows`:
- A PR with a planted bug gets a review whose inline comments are on the right lines,
  a failing `CodeSage` check run, and a job summary with the cost.
- A clean PR gets a passing check.
- A draft PR is skipped until it's marked ready.
- A `/codesage review` comment from the owner triggers a review; the same comment from
  a non-collaborator doesn't.
- The `codesage:review` label triggers a review and is then removed.
- The undocumented items listed in decisions.md are resolved and the docs updated:
  what happens to a comment on a line outside the diff, the permission needed to
  remove the label, and setup-python's current major version.

### Phase 6: Local HITL and console ✅ done

**Changes:**
- `migrations/0002_agentic.sql`, `scripts/migrate.py` and the model update.
- Checkpointer lifecycle.
- Clone workspace for PR mode.
- `interrupt()` gate and `POST /reviews/{id}/decision` (replacing `/approve`).
- `publish` with the idempotency marker.
- Startup reconciliation, SSE and broker fixes.
- Console updates.
- MCP in `report` mode.

**Verify** on a local stack with a throwaway Postgres on a free port:
- A PR review that trips the gate reaches `awaiting_approval`; after an API restart it
  is still awaiting.
- Approving posts to the PR and stores `github_review_url`. A second resume doesn't post twice.
- Rejecting posts nothing and leaves the status `rejected`.
- A passing review is posted automatically.
- A diff-mode review completes without posting.
- Review Detail shows the traces, judge dimensions and cost.
- A row forced to `running` becomes `failed` on restart.
- SSE for a random id returns 404; SSE for a finished review returns its final state and closes.

### Phase 7: Clean-up and docs ✅ done

**Changes:**
- Delete `deploy/` and remove every reference to it (list in
  [design/03](design/03-local-hitl-console.md#removing-deploy)).
- Update the README: the Action as the main usage, the local console as the companion,
  and the repo links.
- Update CLAUDE.md for the new architecture and commands.
- Refresh `docs/workflow.svg`/`.png` (boxes 6–7 and the event chain).
- Mark these docs' status as implemented.

**Verify:**
- `grep -rniE "deploy/|digitalocean|badrinarayanan/CodeSage" --exclude-dir=.git .`
  returns nothing, except history in the docs.
- The standing checks pass.

## Deferred (owner will do these after the features)

- **Eval harness:** a labelled dataset with expected findings, recall/precision, a
  fixed judge, repeats, results stored in `eval_runs`. Today's harness is kept
  runnable in `report` mode but not improved.
- **New automated tests:** mocked-LLM tests for the agent loop and budget, DB-backed
  tests for persistence and resume, SSE tests, and Action tests with recorded event payloads.

## Out of scope for this round

- **Robustness:** typed retry policies, per-reviewer failure isolation, large-diff
  chunking, prompt-injection hardening.
- **Local RAG hardening:** one embedder per index, HNSW, surfaced failures, ingest from a git URL.
- **Hosted deployment** (removed in Phase 7).
