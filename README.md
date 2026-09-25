# CodeSage — Agentic PR Review with Claude

> Three Claude agents review your pull requests the way a senior team would. They
> read the code around the change, search for callers, run your tests, and
> cross-check each other before a single comment is posted.

[![CI](https://github.com/Badri-Rajendran/CodeSage/actions/workflows/ci.yml/badge.svg)](https://github.com/Badri-Rajendran/CodeSage/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

CodeSage runs as a **GitHub Action** on your own repositories:
- **Parallel review:** **security**, **correctness** and **style** agents, built on
  **LangGraph** and **LangChain**, each investigate the change with real tools
  (`read_file`, `search_code`, `git_blame`, `run_tests`, …).
- **Reflection:** a **reflection** agent verifies and consolidates their findings.
- **Judge:** a **judge** on a different model scores the result, and asks for one
  revision round if the review is weak.
- **Output:** one PR review with inline comments, plus a `CodeSage` check run that
  branch protection can require. Every review has a hard cost cap, $0.50 by default.

A **local companion** covers what the Action doesn't: a FastAPI server, a
real-time React console, an MCP server and a pgvector RAG store. It adds a
**real human approval gate**: a review that trips the gate pauses, survives
restarts, and is posted to the PR only once you approve it.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/console-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/console-light.png">
    <img alt="CodeSage Review Console: watch the multi-agent pipeline review a diff in real time" src="docs/screenshots/console-dark.png" width="100%">
  </picture>
  <br>
  <em>The local Review Console streams the agents' progress live (stage timeline, tool-call traces, judge score and cost) over Server-Sent Events.</em>
</p>

---

## How a review works

```
            ┌──────────── security ─────────────┐   each reviewer is a tool-using agent
 PR diff ──▶├──────────── correctness ──────────┤──▶ reflection ──▶ judge ──┬──────────▶ gate ──▶ publish
 + checkout └──────────── style ────────────────┘   (keep/drop/    (score)  │ low score    │
              read_file · search_code · list_dir      merge                  └▶ revise ─▶ judge (once)
              git_log · git_blame · get_diff          decisions)
              run_tests · semantic_search (local)
```

1. **Review.** The three reviewers run in parallel, each deciding what to read,
   search or test. Each finishes with structured findings: severity, confidence,
   file and line, rationale, suggestion, and the evidence it checked.
2. **Reflect.** A reviewer-of-reviewers returns keep/drop/merge decisions, which code
   applies, so every finding keeps the name of the reviewer that raised it. Only
   lines GitHub can comment on stay inline.
3. **Judge.** A different model scores correctness, groundedness, actionability
   and signal-to-noise. A low score triggers one revision round if the budget allows.
4. **Gate.** Any critical or high finding, or a score below the threshold, trips the
   gate. In the Action, the `CodeSage` check fails. Locally, the review pauses for you.
5. **Publish.** One PR review with inline comments, the check run, a job summary,
   and a per-agent cost table.

<p align="center">
  <a href="docs/workflow.svg">
    <img alt="CodeSage review workflow and web-console use-cases" src="docs/workflow.png" width="100%">
  </a>
</p>

## Use it as a GitHub Action

1. Add an `ANTHROPIC_API_KEY` repository secret.
2. Copy [`examples/codesage-workflow.yml`](examples/codesage-workflow.yml) to
   `.github/workflows/codesage.yml`.
3. Optionally, add a [`.codesage.yml`](examples/codesage.yml) at the repo root:

```yaml
models:  {reviewer: claude-sonnet-5, judge: claude-opus-5}
budget_usd: 0.50            # hard cap per review
gate:    {threshold: 0.6, fail_on: [critical, high]}
ignore_paths: ["docs/**"]   # added to built-in ignores (lock files, dist/, minified files)
tests:                      # lets the correctness agent run your tests
  setup: pip install -e ".[dev]"
  command: pytest -q {target}
```

When it runs:

| Trigger | Reviews |
|---|---|
| PR opened (not a draft) or marked ready for review | automatically |
| `codesage:review` label added | again, and the label is removed afterwards |
| `/codesage review` comment by an owner, member or collaborator | again |

- **Forks:** PRs from forks are skipped.
- **Comment triggers:** these only work once the workflow file is on the default
  branch, because GitHub runs `issue_comment` workflows from there.
- **Check run:** `CodeSage` is `success` when nothing blocks, and `failure` when the gate
  trips or the engine errors. It is `neutral` when the only issue is that the budget ran
  out before judging. Make it a required check to block merges.
- **Outputs:** `score`, `gate` (`pass`/`tripped`/`error`), `cost-usd` and `review-url`.

**Security.**
- **What's reviewed:** only same-repo PRs by trusted authors. `pull_request_target` is
  never used.
- **Credentials:** the checkout doesn't persist credentials. Your tests run with API
  keys and tokens removed from their environment.
- **Isolation:** the action runs as a console script, so the reviewed repo's code is
  never imported into the reviewer.

Reproduce a run locally without posting anything:

```bash
pip install .                       # engine only, as the Action installs it
codesage-action review --event event.json --event-name pull_request \
  --repository owner/name --workspace /path/to/checkout --dry-run --local-diff
```

## Local companion

### Quick start (Docker)

```bash
git clone https://github.com/Badri-Rajendran/CodeSage.git
cd CodeSage
cp .env.example .env          # add ANTHROPIC_API_KEY and a CODESAGE_API_KEYS value
docker compose up --build
```

The API requires a key on every `/api/v1` route (see [Security](#security)).
Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`,
put it in `CODESAGE_API_KEYS`, and paste it into the console when it asks.
Set `GITHUB_TOKEN` too if you want to review PRs by number and post approved
reviews.

| Service | URL | What |
| --- | --- | --- |
| **web** | <http://localhost:3000> | Operator console (React UI) |
| **api** | <http://localhost:8000> | REST API + SSE; docs at `/docs` |
| **db** | `localhost:5432` | PostgreSQL + `pgvector` + LangGraph checkpoints |

A fresh database runs every migration automatically. For an existing volume,
run `python -m scripts.migrate`.

### The approval gate

Start a review from the console, or with `POST /api/v1/reviews/async`:

- **PR mode** (a PR number and no diff, with `GITHUB_TOKEN` set). The agents review a
  temporary clone at the PR head. If the gate passes, the review is posted to the PR.
  If it trips, the review **pauses** in `awaiting_approval`.
  - **Approve** posts it; **Reject** posts nothing.
  - Paused reviews survive an API restart, because their checkpoints are in Postgres.
  - Posting is idempotent.
- **Diff mode** (a pasted diff). The same pipeline, but nothing is posted.

Decide from the console (Review Console, Review Detail or Approval Queue), or with the API:

```bash
curl -X POST http://localhost:8000/api/v1/reviews/$ID/decision \
  -H "Authorization: Bearer $CODESAGE_API_KEY" -H 'Content-Type: application/json' \
  -d '{"approved": true, "note": "ship it"}'
```

### Web console

A React + Vite + TypeScript + Tailwind app (`web/`), served by nginx, which proxies
`/api` to the backend so SSE streams straight through.

- **Review Console:** submit a diff or PR and watch the pipeline live. You see the
  stage timeline (reviewers → reflection → judge → revise → gate → publish), the
  agents' tool calls, the judge scorecard, findings with evidence, and cost. Approve
  or reject right there when it pauses.
- **History** and **Review Detail:** every review with its status, gate reasons,
  decision and note, traces, judge dimensions, cost and GitHub link.
- **Approval Queue:** reviews waiting for you, each with an optional note.
- **Cost & Telemetry:** token usage and USD cost per agent.
- **RAG Ingestion:** index a codebase into pgvector for `semantic_search`.
- **Eval & Regression:** LLM-as-Judge runs and model comparisons.

### Screenshots

> The images below **auto-switch** with your GitHub light/dark theme. Every screen
> is built in both themes via a one-click toggle.

<table>
  <tr>
    <td width="50%" valign="top">
      <b>Approval Queue</b> — human-in-the-loop gate
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/approvals-dark.png">
        <img alt="Approval Queue" src="docs/screenshots/approvals-light.png">
      </picture>
    </td>
    <td width="50%" valign="top">
      <b>Cost &amp; Telemetry</b> — token + USD per component
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/telemetry-dark.png">
        <img alt="Cost and Telemetry" src="docs/screenshots/telemetry-light.png">
      </picture>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <b>Eval &amp; Regression</b> — LLM-as-Judge runs + version deltas
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/eval-dark.png">
        <img alt="Eval and Regression" src="docs/screenshots/eval-light.png">
      </picture>
    </td>
    <td width="50%" valign="top">
      <b>RAG Ingestion</b> — index a codebase into pgvector
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/ingest-dark.png">
        <img alt="RAG Ingestion" src="docs/screenshots/ingest-light.png">
      </picture>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <b>History</b> — every review, status &amp; severity at a glance
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/history-dark.png">
        <img alt="Review History" src="docs/screenshots/history-light.png">
      </picture>
    </td>
    <td width="50%" valign="top">
      <b>Review Detail</b> — judge rationale, findings &amp; HITL outcome
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/detail-dark.png">
        <img alt="Review Detail" src="docs/screenshots/detail-light.png">
      </picture>
    </td>
  </tr>
</table>

<details>
<summary><b>Dark &amp; light — same console, one toggle</b></summary>

| Dark | Light |
| --- | --- |
| <img alt="Review Console (dark)" src="docs/screenshots/console-dark.png"> | <img alt="Review Console (light)" src="docs/screenshots/console-light.png"> |

</details>

### Frontend dev (hot reload)

```bash
cd web
npm install
npm run dev      # http://localhost:5173, proxies /api to localhost:8000 (VITE_API_TARGET)
```

### Local dev without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[server,dev]"
export CODESAGE_AUTH_DISABLED=true    # local only; or set CODESAGE_API_KEYS
docker compose up -d db               # just Postgres + pgvector
export DATABASE_URL=postgresql+asyncpg://codesage:codesage@localhost:5432/codesage
export ANTHROPIC_API_KEY=sk-ant-...
python -m scripts.migrate
uvicorn app.main:app --reload
```

Without `ANTHROPIC_API_KEY`, everything runs in an offline **stub mode**
(deterministic heuristics), which is what the test suite uses.

### Ingesting a codebase for RAG

```bash
python -m scripts.ingest_repo --path /path/to/repo --repo owner/name
```

Local reviews can then use `semantic_search` over the indexed code.

### Evaluation & regression detection

```bash
python -m app.eval.harness run --dataset eval/datasets/sample.jsonl
python -m app.eval.harness compare --baseline <model> --candidate <model>
```

### MCP server

```bash
python -m app.mcp.server      # stdio; tools: review_pull_request, review_github_pr
```

It runs the same engine without pausing or posting, and returns findings, gate
reasons, the judge score and cost.

## Configuration

The local server reads environment variables (see [`.env.example`](.env.example)).
The Action uses its inputs and the repo's `.codesage.yml`, which overrides the model,
effort, budget and gate defaults below.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Claude API key (stub mode without it) |
| `CODESAGE_MODEL` | `claude-sonnet-5` | Reviewer and reflection agents |
| `CODESAGE_JUDGE_MODEL` | `claude-opus-5` | Judge (a different model from the reviewers) |
| `CODESAGE_REVIEWER_EFFORT` / `CODESAGE_JUDGE_EFFORT` | `medium` / `high` | Claude effort per role |
| `CODESAGE_BUDGET_USD` | `0.50` | Hard cap on model spend per review |
| `CODESAGE_HITL_THRESHOLD` | `0.6` | Judge score below which the gate trips |
| `GITHUB_TOKEN` | — | Local PR mode: fetch, clone and post reviews |
| `DATABASE_URL` | `postgresql+asyncpg://codesage:codesage@db:5432/codesage` | Postgres DSN |
| `CODESAGE_CHECKPOINT_DSN` | `DATABASE_URL` without `+asyncpg` | LangGraph checkpointer (psycopg) |
| `CODESAGE_EMBED_MODEL` | `voyage-3` (or hash fallback) | Embedding model |
| `CODESAGE_API_KEYS` | — | Comma-separated API keys (**required**; the API fails closed without one) |
| `CODESAGE_AUTH_DISABLED` | `false` | Skip auth entirely: local development only |
| `CODESAGE_INGEST_ROOTS` | — (compose: `/app`) | Directories `POST /ingest` may read; empty disables API ingest |
| `CODESAGE_CORS_ORIGINS` | — | Allowed cross-origin callers; empty allows none |

## Security

- **Authentication.** Every `/api/v1` route except `/api/v1/meta` requires
  `Authorization: Bearer <key>` or `X-API-Key: <key>`, checked against
  `CODESAGE_API_KEYS`. With no keys configured, the API returns 503 rather than
  running open. The console keeps the key in the browser and streams progress with
  `fetch`, so the key never appears in a URL.
- **Agent tools.** Every path is resolved (symlinks too) and confined to the
  repository. `.git` is off limits. Outputs are capped. `run_tests` runs only when the
  repo configures a test command, and gets a scrubbed environment and a
  process-group timeout.
- **Tokens.** The GitHub token is never written to `.git/config`, neither in the
  Action's checkout nor in local clones. Repository names are validated before any
  token-bearing request.
- **Ingestion** over the API is limited to `CODESAGE_INGEST_ROOTS`, with symlinks and
  `..` resolved first. Eval runs over the API only read datasets in `eval/datasets/`.

## Documentation

- [`docs/architecture.md`](docs/architecture.md): components and data flow
- [`docs/design/`](docs/design): the review engine, the GitHub Action, and local mode with the approval gate
- [`docs/decisions.md`](docs/decisions.md): decision log and verified external facts
- [`docs/roadmap.md`](docs/roadmap.md) and [`docs/plans/`](docs/plans): build order and implementation plan

## Tech stack

Python · LangGraph · LangChain · Claude API · GitHub Actions · FastAPI ·
PostgreSQL + pgvector · MCP · React · Docker

## License

MIT — see [LICENSE](LICENSE).
