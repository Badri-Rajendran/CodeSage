# Contributing to CodeSage

Thanks for your interest! This project follows standard Python practices.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[server,dev]"
cp .env.example .env               # add ANTHROPIC_API_KEY (optional — stub mode works without)
```

## Workflow

```bash
make test        # run the suite (runs offline in stub mode, no API key needed)
make lint        # ruff
make fmt         # ruff --fix + format
make typecheck   # mypy
```

- The suite must stay green **without** an API key or database — core logic runs
  in deterministic stub mode (see `app/llm/stub.py`).
- Keep new code typed and covered by a test.
- Conventional, present-tense commit messages (`add`, `fix`, `refactor`).

## Project layout

| Path | What |
| --- | --- |
| `app/agents/` | LangGraph orchestration, ReAct reviewers, reflection, sandbox |
| `app/rag/`, `app/db/vector_store.py` | pgvector RAG pipeline |
| `app/eval/` | LLM-as-Judge harness + regression detection |
| `app/llm/` | Claude client, telemetry, prompts, offline stub |
| `app/api/`, `app/mcp/` | REST and MCP surfaces |

## Adding a reviewer

Subclass `app.agents.reviewers.base.ReviewerAgent`, set `name`/`component`/
`system`/`focus_query_template`, register it in `app/agents/graph.py`, and add a
stub branch in `app/llm/stub.py` so it works offline.
