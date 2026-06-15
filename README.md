# CodeSage — Agentic Code Review Assistant

> A Claude-powered multi-agent system that reviews pull requests the way a senior
> team would — reasoning, reflecting, and citing context from the codebase before
> it comments.

[![CI](https://github.com/badrinarayanan/CodeSage/actions/workflows/ci.yml/badge.svg)](https://github.com/badrinarayanan/CodeSage/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

CodeSage ingests a pull request, retrieves relevant context from the surrounding
codebase with a **pgvector RAG pipeline**, then runs **parallel security,
correctness, and style reviewer agents** built on **LangGraph** (ReAct +
self-reflection). Findings are aggregated, de-duplicated, run through an
**LLM-as-Judge** eval harness, and surfaced behind a **human-in-the-loop gate**.
Every model call is metered for **token-cost telemetry**, and the agents are
exposed over both a **REST API**, an **MCP server**, and a **real-time web console**.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/console-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/console-light.png">
    <img alt="CodeSage Review Console — watch the multi-agent pipeline review a diff in real time" src="docs/screenshots/console-dark.png" width="100%">
  </picture>
  <br>
  <em>The Review Console streams the agents' progress live — stage timeline, ReAct traces, judge score, and cost — over Server-Sent Events.</em>
</p>

---

## Highlights

| Capability | Where it lives |
| --- | --- |
| Multi-agent PR review on LangGraph (ReAct + self-reflection) | [`app/agents/`](app/agents) |
| Parallel security / correctness / style reviewers | [`app/agents/reviewers/`](app/agents/reviewers) |
| pgvector RAG pipeline on PostgreSQL | [`app/rag/`](app/rag), [`app/db/vector_store.py`](app/db/vector_store.py) |
| Sandboxed test execution | [`app/agents/sandbox.py`](app/agents/sandbox.py) |
| Human-in-the-loop gate | [`app/agents/graph.py`](app/agents/graph.py) |
| LLM-as-Judge eval harness + cross-version regression detection | [`app/eval/`](app/eval) |
| Token-cost telemetry | [`app/llm/telemetry.py`](app/llm/telemetry.py) |
| REST API (FastAPI) | [`app/api/`](app/api) |
| MCP server | [`app/mcp/server.py`](app/mcp/server.py) |
| Containerized + deployable (Docker / DigitalOcean / AWS) | [`Dockerfile`](Dockerfile), [`deploy/`](deploy) |
| Real-time web console (React + Vite + SSE) | [`web/`](web) |

## Workflow & use-cases

One review flows left-to-right through the pipeline; progress streams to the web
console in real time. Each console view below maps to something you can *do* with
the system — run reviews, clear the human-in-the-loop gate, watch cost, ingest a
codebase for RAG, or evaluate models for regressions.

<p align="center">
  <a href="docs/workflow.svg">
    <img alt="CodeSage review workflow and web-console use-cases" src="docs/workflow.png" width="100%">
  </a>
</p>

## Architecture

```
                         ┌────────────────────────────────────────────┐
   PR diff ──▶ Ingest ──▶│  LangGraph orchestration (ReAct loop)        │
   (GitHub /             │                                              │
    REST / MCP)          │   ┌──────────┐  ┌────────────┐  ┌────────┐  │
                         │   │ Security │  │ Correctness │  │ Style  │  │ ← parallel
        ▲                │   │ reviewer │  │  reviewer   │  │reviewer│  │   reviewers
        │                │   └────┬─────┘  └─────┬───────┘  └───┬────┘  │
   pgvector RAG ◀────────┼────────┴──── tools ───┴──────────────┘       │
   (codebase context)    │              (retrieve_context,              │
                         │               run_sandboxed_tests)           │
                         │                     │                        │
                         │              Aggregate + self-reflect        │
                         │                     │                        │
                         │              Human-in-the-loop gate          │
                         └─────────────────────┬────────────────────────┘
                                               │
                                  LLM-as-Judge eval harness
                                  + cross-version regression
                                               │
                                       Review report
```

Every node records token usage and cost via the telemetry layer.

## Quick start (local, Docker)

```bash
git clone https://github.com/badrinarayanan/CodeSage.git
cd CodeSage
cp .env.example .env          # add your ANTHROPIC_API_KEY
docker compose up --build
```

This starts three services wired together by `docker-compose.yml`:

| Service | URL | What |
| --- | --- | --- |
| **web** | <http://localhost:3000> | Operator console (React UI) |
| **api** | <http://localhost:8000> | REST API + SSE; docs at `/docs` |
| **db** | `localhost:5432` | PostgreSQL + `pgvector` |

Open <http://localhost:3000> for the web console; the API is also usable directly.

## Web console (operator UI)

A React + Vite + TypeScript + Tailwind single-page app (`web/`) served by nginx,
which reverse-proxies `/api` to the backend (so SSE streams straight through).

- **Review Console** — submit a diff and watch the multi-agent pipeline run in
  **real time** over Server-Sent Events: a live stage timeline (security /
  correctness / style → reflection → judge → human gate), streamed activity log,
  judge scorecard, findings, and ReAct reasoning traces.
- **History** — every review, with status, judge score, and severity breakdown.
- **Approval Queue** — act on the human-in-the-loop gate (approve / reject).
- **Cost & Telemetry** — token usage and USD cost per pipeline component.
- **RAG Ingestion** — index a codebase into pgvector from the browser.
- **Eval & Regression** — launch LLM-as-Judge runs and cross-version comparisons.
- Dark / light theme toggle.

The real-time path is backed by async review jobs (`POST /api/v1/reviews/async`)
that stream progress events from an in-process broker over
`GET /api/v1/reviews/{id}/stream` (SSE). The original synchronous
`POST /api/v1/reviews` is unchanged.

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
npm run dev      # http://localhost:5173, proxies /api to localhost:8000
```

### Run a review

```bash
curl -X POST http://localhost:8000/api/v1/reviews \
  -H 'Content-Type: application/json' \
  -d '{
    "repo": "octocat/hello-world",
    "pr_number": 1,
    "diff": "diff --git a/app.py b/app.py\n@@ -1 +1,2 @@\n-print(1)\n+import os\n+os.system(input())"
  }'
```

You'll get back findings grouped by reviewer, a judge score, and a
`requires_human_approval` flag if the gate tripped.

## Local dev without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# bring up just Postgres+pgvector:
docker compose up -d db
export DATABASE_URL=postgresql+asyncpg://codesage:codesage@localhost:5432/codesage
export ANTHROPIC_API_KEY=sk-ant-...
python -m scripts.init_db
uvicorn app.main:app --reload
```

## Ingesting a codebase for RAG

```bash
python -m scripts.ingest_repo --path /path/to/repo --repo owner/name
```

This chunks source files, embeds them, and stores vectors in pgvector so the
reviewers can cite real surrounding code.

## Evaluation & regression detection

```bash
# Score a batch of reviews with the LLM-as-Judge harness
python -m app.eval.harness run --dataset eval/datasets/sample.jsonl

# Compare two model versions and flag regressions
python -m app.eval.harness compare \
  --baseline claude-opus-4-7 --candidate claude-opus-4-8
```

## MCP server

```bash
python -m app.mcp.server      # stdio MCP server exposing review_pull_request
```

Point any MCP-compatible client (Claude Desktop, etc.) at it to call CodeSage's
review tools directly.

## Deployment

See [`deploy/README.md`](deploy/README.md) for the DigitalOcean droplet path
(one `deploy.sh` invocation) and notes on the AWS (ECS/Lambda) story.

## Configuration

All settings are environment-driven; see [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Claude API key (required) |
| `CODESAGE_MODEL` | `claude-opus-4-8` | Primary reviewing model |
| `CODESAGE_JUDGE_MODEL` | `claude-opus-4-8` | LLM-as-Judge model |
| `CODESAGE_EMBED_MODEL` | `voyage-3` (or hash fallback) | Embedding model |
| `DATABASE_URL` | `postgresql+asyncpg://codesage:codesage@db:5432/codesage` | Postgres DSN |
| `CODESAGE_EFFORT` | `high` | Claude effort level |
| `CODESAGE_HITL_THRESHOLD` | `0.6` | Judge score below which human approval is required |

## Tech stack

Python · FastAPI · LangGraph · LangChain · Claude API · pgvector · PostgreSQL ·
MCP · Docker · DigitalOcean / AWS

## License

MIT — see [LICENSE](LICENSE).
