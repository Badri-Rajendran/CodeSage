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
| `app/agents/` | Review graph, tool-using reviewer agents, reflection, middleware, schemas |
| `app/workspace/`, `app/diff/` | Agent tools over a checkout (confined, capped), diff model |
| `app/llm/` | Model factory, budget guard, telemetry, prompts, offline stub |
| `app/eval/` | LLM-as-Judge (also the graph's judge), eval harness, regression detection |
| `app/action/`, `app/publish/`, `action.yml` | GitHub Action runner and GitHub publishing |
| `app/api/`, `app/services/`, `app/mcp/` | Local REST/SSE API, review jobs + approval gate, MCP |
| `app/rag/`, `app/db/` | pgvector RAG store, ORM models (mirror `migrations/*.sql`) |
| `web/` | React operator console |

## Adding a reviewer

1. Add the role to `REVIEWER_ROLES` (`app/agents/reviewers/base.py`) and to the
   `ReviewerName` literal (`app/agents/schemas.py`); give it an id prefix in
   `app/agents/findings.py`.
2. Write its focus prompt in `REVIEWER_FOCUS` (`app/llm/prompts.py`).
3. Add a stub branch in `app/llm/stub.py` so it works offline.
4. Add the stage to the console: `web/src/lib/format.ts` (`STAGES`),
   `web/src/lib/useReviewStream.ts` (`STAGE_DEPS`, `STAGE_LABEL`),
   `web/src/lib/types.ts` (`StageName`) and `web/src/components/StageTimeline.tsx`
   (`ICONS`). The backend's stage list derives from `REVIEWER_ROLES`.
