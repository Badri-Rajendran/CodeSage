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
exposed over both a **REST API** and an **MCP server**.

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

The API comes up on <http://localhost:8000> with interactive docs at
<http://localhost:8000/docs>. Postgres (with the `pgvector` extension) and the
app are wired together by `docker-compose.yml`.

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
