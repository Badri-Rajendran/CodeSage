# Design 03: Local Mode, Real Approval Gate, Console and Job Fixes

> Status: approved design (2026-09-23). Local mode is the owner's on-demand
> companion to the GitHub Action. It isn't hosted and isn't connected to Action runs.

## Goals

- A **real** human-in-the-loop gate: the graph pauses, survives a restart, and
  resumes on a decision. **Approve** posts the review to the PR, **reject** doesn't,
  and a **passing** gate posts automatically.
- Review Detail shows everything the live console shows: traces, judge dimensions and cost.
- No review stuck in `running`, and no SSE stream that never ends.
- The MCP server keeps working as a local way to start reviews.

## Graph modes

`build_review_graph(deps, *, mode, checkpointer=None)` compiles one graph in one of three modes:

| Mode | Used by | `human_gate` | `publish` | Checkpointer |
|---|---|---|---|---|
| `action` | GitHub Action | Computes `gate_reasons`; never pauses | `GitHubPublisher`: review plus check run | none |
| `local` | FastAPI jobs | `interrupt()` when `gate_reasons` is non-empty | `GitHubPublisher.post_review` (review only) when approved or passing, and a PR is known | `AsyncPostgresSaver` |
| `report` | MCP, eval harness | Computes `gate_reasons`; never pauses | none | none |

Local mode posts **no check run**: check-run writes are limited to GitHub Apps, which
includes `GITHUB_TOKEN` in Actions, and personal access tokens can't create them **(verified)**.

## Starting a local review

`POST /api/v1/reviews/async` takes the same body as today (`repo`, `pr_number?`, `diff?`):

- **PR mode** (`pr_number` given, `GITHUB_TOKEN` set):
  - Fetch the PR (`GitHubClient.get_pr`: title, body, base/head SHAs, head repo) and its diff.
  - Clone the repo at the head commit into a temporary directory with
    `Workspace.clone_at(repo, head_sha, base_sha, token)`:
    `git clone --filter=blob:none --no-checkout`, fetch both SHAs, then check out the head.
  - The token is passed with `git -c http.extraHeader=...` for each command, so it's
    never written to `.git/config`.
  - Tools: everything except `run_tests`, plus `semantic_search`, which uses the pgvector
    index if the repo was ingested.
- **Diff mode** (only `diff` given):
  - No workspace. The agents get `get_diff` and `semantic_search`.
  - `publish` is a no-op, because there's no PR to post to. The console says so.
- The temporary clone is deleted once the graph returns, whether it finished or paused
  at the gate. Nothing after the gate needs the workspace.

## Pause and resume

- **Checkpointer:** in the FastAPI lifespan, open
  `AsyncPostgresSaver.from_conn_string(settings.checkpoint_dsn)`, run `await setup()`
  once, and keep it for the app's lifetime.
  - `checkpoint_dsn` comes from `CODESAGE_CHECKPOINT_DSN`, or by default from
    `DATABASE_URL` with `+asyncpg` removed. The saver uses **psycopg v3**, not
    asyncpg **(verified)**.
  - `thread_id` = the review id.
- **`human_gate` (local mode):**

  ```python
  def human_gate(state):
      reasons = compute_gate_reasons(state, cfg)          # pure; safe to re-run
      if not reasons:
          return {"gate_tripped": False, "gate_reasons": []}
      decision = interrupt({"reasons": reasons, "score": state.get("judge_score")})
      return {"gate_tripped": True, "gate_reasons": reasons,
              "decision": "approved" if decision["approved"] else "rejected",
              "decision_note": decision.get("note")}
  ```

  On resume the node **re-runs from the start** **(verified)**. That's safe because
  `compute_gate_reasons` has no side effects.
- **Detecting the pause:** `astream(stream_mode="updates")` yields an `__interrupt__` key
  **(verified)**. `_execute_graph` sees it, sets the row to `awaiting_approval`, and
  publishes `review.awaiting_approval` with `{review_id, gate_reasons, judge_score}`.
  `_merge_delta` and `_stage_event` ignore `__interrupt__`.
- **Deciding:** `POST /api/v1/reviews/{id}/decision` with `{approved: bool, note?: str}`.
  - Returns 409 unless the status is `awaiting_approval`.
  - Stores `decision`, `decision_note` and `decided_at`.
  - Submits a resume job to `JobManager`, which rebuilds the graph (the deps aren't
    serialized) and calls `astream(Command(resume={"approved": ..., "note": ...}), config)`,
    streaming events on the same SSE channel.
  - Returns 202.
- **`publish` (local mode):**
  - Posts only if a PR is known **and** (the gate didn't trip **or** the decision is `approved`).
  - **Idempotency:** the review body carries a hidden marker
    `<!-- codesage-review:{review_id} -->`. Before posting, `publish` lists the PR's
    reviews and skips posting if the marker is already there. This makes a re-run of
    the node after a crash, or a repeated resume, harmless.
  - Stores `github_review_url` on the row.
- **Rejected:** the status becomes `rejected` and nothing is posted.
- **Replacing today's `POST /reviews/{id}/approve`:** that endpoint only flipped a flag.
  It's removed, and the console calls `/decision` instead. Nothing outside this repo
  uses it (verified by the audit).

## Review statuses

`running` → (`awaiting_approval` → `completed` | `rejected`) | `completed` | `failed`

- `completed` covers both "posted" and "diff mode, nothing to post"; `github_review_url` tells them apart.
- `requires_human_approval` stays in `ReviewResponse` for compatibility, computed as
  `status == "awaiting_approval"`. `approved` is computed from `decision`. Neither is
  cleared any more, and `gate_tripped` keeps the history.

## Persistence

### Migration `migrations/0002_agentic.sql`

Idempotent (`ADD COLUMN IF NOT EXISTS`). New columns on `reviews`:

| Column | Type | Purpose |
|---|---|---|
| `traces` | JSONB NOT NULL DEFAULT '[]' | Agent traces (fixes the empty traces card in Review Detail) |
| `judge_dimensions` | JSONB | Judge meters |
| `telemetry` | JSONB | `CostTracker.summary()` (fixes the empty cost and token counts) |
| `base_sha`, `head_sha`, `pr_title` | TEXT | PR context |
| `gate_tripped` | BOOLEAN NOT NULL DEFAULT false | Gate history (no longer lost on approval) |
| `gate_reasons` | JSONB NOT NULL DEFAULT '[]' | Why it tripped |
| `budget_limited` | BOOLEAN NOT NULL DEFAULT false | Budget note |
| `revision_count` | INTEGER NOT NULL DEFAULT 0 | Whether a revise round ran |
| `decision` | TEXT | `approved` or `rejected` |
| `decision_note` | TEXT | Operator note |
| `decided_at` | TIMESTAMPTZ | When |
| `github_review_url` | TEXT | Where it was posted |
| `error` | TEXT | Failure message (instead of overwriting `summary`) |

- `app/db/models.py` is updated to match. The two schema sources stay in sync by rule
  (CLAUDE.md), and both must change together.
- **Applying it:** a new `scripts/migrate.py` runs every `migrations/*.sql` in order. They're
  all idempotent, so running them again is safe. `docker-compose.yml` mounts the whole
  `migrations/` directory into `docker-entrypoint-initdb.d`, so fresh databases get
  every migration. Existing volumes need `python -m scripts.migrate`.
- **Checkpointer tables** are created by `AsyncPostgresSaver.setup()` and aren't part of our migrations.

### What `ReviewService` writes

Today `traces`, `judge_dimensions` and the telemetry summary are dropped when the row
is saved (the audit found this). Now `run_review_streamed` and the resume path
persist every new column, `_review_to_dict` returns them, and `ReviewResponse` adds
`gate_reasons`, `budget_limited`, `revision_count`, `decision`, `decision_note`,
`decided_at`, `github_review_url`, `head_sha`, `pr_title` and `error`.

## Job and stream fixes

| Problem (found in the audit) | Fix |
|---|---|
| Rows stuck in `running` after a restart | On startup, `UPDATE reviews SET status='failed', error='interrupted by restart' WHERE status='running'`. `awaiting_approval` rows are left alone; their checkpoints are in Postgres. |
| SSE for an unknown id sends keep-alives forever | The stream route checks the DB first: 404 if the row doesn't exist. |
| SSE for a finished review (not in the broker) hangs | If the row is `completed`, `failed` or `rejected`, send one final `review.completed` or `review.failed` built from the DB, then close. If it's `awaiting_approval`, send `review.awaiting_approval`, then close. |
| Broker buffers are never pruned | After a terminal or awaiting event, schedule eviction of that review's buffer 10 minutes later. |
| `uuid.UUID(bad)` raises a 500 | Review ids are validated as UUIDs; a bad id returns 404. |

## Console changes (`web/`)

| File | Change |
|---|---|
| `lib/types.ts` | `ReviewStatus` = running, awaiting_approval, completed, rejected, failed; new `ReviewAwaitingApprovalEvent`; new `Review` fields; `StageName` adds `revise` and `publish` |
| `lib/api.ts` | `decide(id, approved, note?)` replaces `approve` |
| `lib/useReviewStream.ts` | `STAGE_DEPS` adds `revise: ["judge"]` and `publish: ["human_gate"]`; `revise` shows as skipped when `human_gate` completes without it; handles `review.awaiting_approval` (status `awaiting`) |
| `components/StageTimeline.tsx` | Two new stages; a "skipped" style |
| `pages/ReviewConsole.tsx` | The approval banner shows on `awaiting_approval` (not `completed`); after a decision it keeps streaming until `publish` finishes; shows the GitHub link |
| `pages/ReviewDetail.tsx` | Status chip, gate reasons, traces, judge dimensions, cost and tokens (from the persisted `telemetry`), decision and note, GitHub link, error |
| `pages/ApprovalQueue.tsx` | Filters on `status === "awaiting_approval"`; optional note field; calls `decide` |
| `components/ReviewRow.tsx` | Chips for the new statuses |

The backend's `STAGE_ORDER` (`app/services/review_service.py`) adds `revise` and
`publish`, in step with the frontend. That's the duplication rule in CLAUDE.md.

## MCP server

`app/mcp/server.py` builds the graph in `report` mode, with the same engine, no
checkpointer and no publishing. For `review_github_pr` it uses a clone workspace, as
PR mode does. It returns findings, `gate_reasons`, the judge score and the telemetry. It
still doesn't save to the DB (unchanged; out of scope).

## Removing deploy

`deploy/` is deleted. References are removed from:

- README.md: the capability table row, the "Deployment" section, and the Security bullets
  that mention the prod compose and `deploy.sh`
- CLAUDE.md: the deployment line
- CONTRIBUTING.md: the layout table row
- `.env.example`: the "forced off by deploy/…" comment
- `app/config.py`: the module docstring (DigitalOcean/AWS)
- `.dockerignore`

The local `docker-compose.yml`, which runs the local stack, stays. The README's repo
links move to `Badri-Rajendran/CodeSage`.
