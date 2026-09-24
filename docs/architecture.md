# CodeSage Architecture

> Status: **approved design, not yet implemented** (2026-09-23). The current code
> differs; see [roadmap.md](roadmap.md) for the build order and
> [decisions.md](decisions.md) for why each choice was made.

## What CodeSage is

CodeSage is a personal pull-request reviewer for the owner's own Python and
TypeScript/JavaScript repositories. Its main job is to run as a **GitHub Action**
on each PR: three agents review the change in parallel, reading and searching the
checked-out code and, where configured, running the repo's tests. Their findings
are consolidated, scored by a judge, and posted to the PR as a review with inline
comments and a check run.

The FastAPI server, React console, MCP server and Postgres from the earlier
version stay on, as a **local companion tool**: on-demand reviews, a real human
approval gate, cost telemetry and RAG ingestion. It isn't connected to Action runs.

## The two modes

| | GitHub Action (primary) | Local (laptop) |
|---|---|---|
| Entry point | `python -m app.action`, run by a composite `action.yml` | FastAPI (`/api/v1/reviews…`), web console, MCP |
| Trigger | PR opened / ready for review; `codesage:review` label; `/codesage review` comment | You, from the console, API, or MCP |
| Code context | Runner checkout at the PR's head commit (full history) | Temporary clone at the head commit (by PR number), plus pgvector `semantic_search` |
| Tests | `run_tests` tool, if `.codesage.yml` sets `tests.command` | Not available (the runner is the only isolated environment) |
| Human gate | Never pauses; the `CodeSage` check run fails when the gate trips | Genuinely pauses (`interrupt()`); approve posts to GitHub, reject does not |
| State | Ephemeral; results live in GitHub (review, check run, job summary) | Postgres: reviews, usage events, LangGraph checkpoints |
| Posting | Always posts | Posts when the gate passes, or after approval |

## Component map

```
 ┌── GitHub Action (runner) ──────────┐      ┌── Local (laptop) ────────────────────────┐
 │ action.yml (composite)             │      │ FastAPI routes ─ JobManager ─ Broker(SSE)│
 │   └─ app.action  (event → rules)   │      │ web console (React)   MCP server         │
 │        checkout @ head SHA         │      │ Workspace.clone_at(repo, head_sha)       │
 └──────────────┬─────────────────────┘      └───────────────┬──────────────────────────┘
                │                                            │
                ▼                                            ▼
        ┌──────────────────────── review engine (app/agents) ─────────────────────────┐
        │  inputs: PRContext + Diff (app/diff) + Workspace (app/workspace)             │
        │          + BudgetGuard (app/llm/budget) + ReviewConfig (.codesage.yml)       │
        │                                                                              │
        │  START ─┬─▶ security ──┐     each reviewer = wrapper node → create_agent     │
        │         ├─▶ correctness┼─▶ reflection ─▶ judge ─┬────────────▶ human_gate    │
        │         └─▶ style ─────┘   (decisions)          │  score<0.6 &   │           │
        │                                                 └─▶ revise ─▶ judge (once)   │
        │                                                                  ▼           │
        │                                                               publish ─▶ END │
        └──────────────────────────────────────┬───────────────────────────────────────┘
                                               ▼
                 Publisher: GitHubPublisher (review + check run)  |  ConsolePublisher (DB + SSE)
```

## Units and their interfaces

Each unit has one job and can be understood without reading the others' internals.

| Unit | Location | Responsibility | Depends on |
|---|---|---|---|
| **Diff model** | `app/diff/` (new) | Parse a unified diff into files and hunks with RIGHT-side line ranges; `is_commentable(path, line)` | nothing |
| **Workspace** | `app/workspace/` (new) | A repo at a commit, plus the agent tools over it (`read_file`, `search_code`, `list_dir`, `git_log`, `git_blame`, `get_diff`, `run_tests`); path confinement and output caps; `clone_at()` for local mode | git |
| **Review config** | `app/review_config.py` (new) | Load and validate `.codesage.yml` with defaults; used by both modes (local mode uses the defaults plus env settings) | pyyaml, pydantic |
| **Model factory** | `app/llm/models.py` (new) | Build `ChatAnthropic` per role (reviewer, reflection, judge) from settings and config | langchain-anthropic |
| **Budget guard** | `app/llm/budget.py` (new) | Price every model call; per-agent allowances plus a reserve; tell agents when to stop | `app/llm/telemetry.py` |
| **Telemetry** | `app/llm/telemetry.py` (kept, extended) | `CostTracker`, `cost_usd`, price table (adds the Claude 5 models) | nothing |
| **Reviewer agents** | `app/agents/reviewers/` (rewritten) | `create_agent` + tools + `ProviderStrategy(Findings)` + middleware (budget, call limit, trace) | model factory, workspace, budget |
| **Reflection agent** | `app/agents/reflection.py` (rewritten) | Return per-finding decisions; code applies them | model factory, workspace (read-only tools) |
| **Judge** | `app/eval/judge.py` (rewritten) | One structured Opus 5 call scoring four dimensions | model factory |
| **Review graph** | `app/agents/graph.py` (extended) | The outer `StateGraph`; optional checkpointer; mode-specific gate | all of the above |
| **Stub** | `app/llm/stub.py` (kept) | Deterministic offline output when no API key is set (tests and CI) | nothing |
| **GitHub client** | `app/github/client.py` (extended) | `fetch_pr_diff`, `get_pr`, `create_review`, `create_check_run`, `update_check_run`, `remove_label` | httpx |
| **Publishers** | `app/publish/` (new) | `GitHubPublisher` (review, inline comments, check run, markdown) and `ConsolePublisher` (DB and SSE) | GitHub client, DB |
| **Action runner** | `app/action/` (new) + `action.yml` | Event rules, fork skip, head-SHA lookup, run the engine, publish, job summary | engine, publishers |
| **Local services** | `app/services/`, `app/api/`, `web/` (extended) | Async jobs, SSE, interrupt/resume, decision endpoint, persistence, console | engine, DB, checkpointer |

## Data flow for one review

1. **Assemble the input.** The Action reads the event payload (and calls `GET /pulls/{n}` for comment triggers); local mode fetches the PR. Either way this produces a `PRContext`: repo, number, title, body, base and head SHAs. The diff is parsed by `app/diff`, and ignored paths from `.codesage.yml` are filtered out.
2. **Prepare the workspace.** The Action uses the runner checkout (`fetch-depth: 0`); local mode clones the repo at the head commit into a temporary directory.
3. **Fan out.** The three reviewer nodes run in parallel. Each runs a `create_agent` loop: Claude calls tools, observes the results, and finishes with a structured `Findings` object. The budget middleware tracks spend after every model call.
4. **Reflect.** The reflection agent sees all the draft findings (each with an `id` and a `reviewer`) and returns decisions. Code applies them, so attribution can't be lost, and findings are checked against the diff model.
5. **Judge.** Opus 5 scores the consolidated review. If the score is below the threshold and enough budget remains, the graph goes through `revise` (reflection again, given the judge's rationale) and back to the judge. This happens at most once.
6. **Gate.** The gate trips when any finding is critical or high, or when the score is below the threshold. In the Action this sets the check-run conclusion. Locally it calls `interrupt()` and waits for a decision.
7. **Publish.** One GitHub review with validated inline comments, plus the check run (Action) or a DB update and SSE event (local), plus a cost summary.

## State

The outer graph state (`app/agents/state.py`) keeps the current keys and adds:

- `pr` (the `PRContext` dict), `budget_limited: bool`, `revision_count: int`
- `gate_tripped: bool`, `gate_reasons: list[str]`, `decision: "approved" | "rejected" | None`, `decision_note: str | None` (local mode, set on resume)
- `github_review_url: str | None`
- **Additive reducers:** `draft_findings`, `traces`. `_merge_delta` in `review_service.py` must list the same keys.
- Findings carry `id`, `reviewer`, `category`, `severity`, `confidence`, `path`, `line`, `end_line`, `title`, `rationale`, `suggestion` and `evidence`.

Each reviewer's own message history lives inside its `create_agent` subgraph and isn't copied to the outer state; only its structured findings and trace are.

## Cross-cutting rules

- **Budget.** The $0.50 total is enforced by `BudgetGuard`, as set out in [design/01-agent-engine.md](design/01-agent-engine.md#budget).
- **Stub mode.** With no `ANTHROPIC_API_KEY`, reviewer, reflection and judge nodes use `app/llm/stub.py` and emit scripted traces. The existing test suite depends on this.
- **Secrets.** The Anthropic key and `GITHUB_TOKEN` are never passed to `run_tests` subprocesses. The local API keeps the API-key authentication added in commit `51be21f`.
- **Trace shape.** `{agent, steps: [{kind: thought|action|observation, content, tool?}]}` is the contract with the console (`web/src/lib/types.ts`).
- **Stage names.** They're duplicated in `STAGE_ORDER` (`app/services/review_service.py`), `STAGE_DEPS`/`STAGE_LABEL` (`web/src/lib/useReviewStream.ts`), `StageTimeline.tsx` and `stub.py`. New stages (`revise`, `publish`) have to be added to all of them.

## Design documents

- [design/01-agent-engine.md](design/01-agent-engine.md): reviewers, tools, budget, reflection, judge, revision, stub mode
- [design/02-github-action.md](design/02-github-action.md): packaging, triggers, `.codesage.yml`, publishing, check run
- [design/03-local-hitl-console.md](design/03-local-hitl-console.md): interrupt/resume, persistence, console and job fixes, MCP
- [decisions.md](decisions.md): decision log and verified external facts
- [roadmap.md](roadmap.md): phases, verification, deferred work
