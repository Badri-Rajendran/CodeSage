# Decisions and Verified Facts

Every decision below was made by the project owner during the design session on
2026-09-23, after a whole-project audit found the agentic workflows to be early
versions rather than finished ones. Where a decision rests on an external fact,
the fact and its source are listed in [Verified external facts](#verified-external-facts).

## Decision log

| # | Topic | Decision | Alternatives considered | Why |
|---|---|---|---|---|
| D1 | Purpose | A **personal real tool** used day to day on the owner's own repos; one user | Portfolio showcase; team product; showcase now, product later | Sets the quality bar: reliable and cheap, not multi-tenant |
| D2 | Trigger | **GitHub Action** | Webhook to a hosted server; on-demand only; webhook plus on-demand | No always-on server; runs where the code already is |
| D3 | Server role | **Action is self-contained; console stays local** | Action calls a hosted server; Action reports to a server; drop the server | Zero hosting cost; the console stays useful on-demand |
| D4 | Code context | **File tools on the checkout** (git-based) | Per-run pgvector; file tools plus a cached semantic index | Fresh, exact, no embedding cost; pgvector stays for local mode |
| D5 | Action gate | **Always post; the check run fails when the gate trips** | Hold until approved (Environments); REQUEST_CHANGES; no gate in CI | Visible, can block merge through branch protection, never stalls |
| D6 | Local gate | **Real pause; approve posts to GitHub** | Real pause without posting; keep the flag | Makes the approval queue meaningful |
| D7 | Languages | **Python, TypeScript/JavaScript** | Go/Rust/Java; language-agnostic | The owner's repos |
| D8 | Tests | **Agent-invoked `run_tests`** running the repo's own command on the runner | Always run tests first; no tests | The agent picks what to test; the runner is a disposable VM |
| D9 | Models | **Sonnet 5 reviewers, Opus 5 judge** | Opus reviewers with a Sonnet judge; all Sonnet; all Opus | The reviewers use most of the tokens; a stronger, different judge avoids self-grading |
| D10 | Packaging | **Reusable composite action** `Badri-Rajendran/CodeSage@v1` | Reusable workflow; copy-in workflow | Versioned; small per-repo workflow; runs on any OS |
| D11 | PR authors | **Owner and collaborators only; forks skipped** | Outside contributors; include bots | Simplest safe model: secrets are never exposed to untrusted code |
| D12 | Eval and tests | **Deferred**: the owner will build the eval harness and new tests after the features | Seeded-bug benchmark; real past PRs; judge-only fix | The owner's sequencing choice; the existing 70 tests must stay green |
| D13 | Re-reviews | **Only when asked** (label or `/codesage review`) after the automatic review on open | Incremental on every push; full re-review every push; open + ready only | Cost control |
| D14 | Budget | **$0.50 hard cap per review** | $1; $2; no cap | Cost control; configurable per repo |
| D15 | Scope | **In:** reflection/judge loop, console/job fixes. **Out:** robustness bundle, local RAG hardening | | The owner's scope choice |
| D16 | Deploy | **Remove `deploy/`** (DigitalOcean/AWS) | Keep as is; keep and fix | The console is local-only (D3), so a hosted deploy has no role |
| D17 | Agent framework | **LangGraph/LangChain prebuilt agent** (`create_agent`, the successor to `create_react_agent`) | Own tool loop over the Anthropic SDK (recommended at the time); SDK beta tool runner | The owner's choice. Consequences: new dependencies, and the budget-forcing recipe needs a spike (see below) |
| D18 | Local passing gate | **Post automatically** | Always ask; never post passing reviews | Same behaviour as the Action |
| D19 | Architecture sections 1–5 | Approved as presented (shared engine, budget split, Action flow, local HITL/console, docs + phases) | | See the design docs |

### Consequences recorded at decision time

- **D17** means installing `langchain` and `langchain-anthropic`, and upgrading
  `anthropic` (0.109.1 → ≥0.120), `langgraph` (1.2.5 → ≥1.2.11) and `langchain-core`
  (1.4.7 → ≥1.6.4). The budget "finish now" behaviour and `ProviderStrategy` with tools and
  adaptive thinking on Sonnet 5 aren't documented recipes, so Phase 1 proves them against
  the live API before anything is built on them.
- **D12:** until the owner adds tests, the new agent code is covered only by the
  manual end-to-end checks in [roadmap.md](roadmap.md).
- **D6** needs `langgraph-checkpoint-postgres`, which uses psycopg v3 and a
  connection string separate from the app's asyncpg one.

## Verified external facts

Checked on 2026-09-23. "Installed source" means the project's `.venv` at that date.

### GitHub Actions and GitHub API

| Fact | Source |
|---|---|
| Docker container actions run only on Linux runners and are slower than JavaScript actions; composite actions run on any OS | https://docs.github.com/en/actions/sharing-automations/creating-actions/about-custom-actions |
| Composite steps may `uses:` other actions; `run:` steps need `shell:`; `github.action_path` is only available in composite actions; inputs come from `inputs` (no automatic `INPUT_*` env) | https://docs.github.com/en/actions/reference/workflows-and-actions/metadata-syntax · https://docs.github.com/en/actions/reference/workflows-and-actions/contexts |
| `pull_request` activity types include `opened`, `ready_for_review` and `labeled`; the label is at `github.event.label.name` | https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows |
| `issue_comment` runs the workflow from the **default branch** (GITHUB_SHA is the last commit on the default branch) and only if the workflow file exists there; detect a PR with `github.event.issue.pull_request`, whose object has no head SHA | same page; payload schema in https://github.com/github/rest-api-description |
| `author_association` values: COLLABORATOR, CONTRIBUTOR, FIRST_TIMER, FIRST_TIME_CONTRIBUTOR, MANNEQUIN, MEMBER, NONE, OWNER | https://github.com/github/rest-api-description (`author-association` schema) |
| Secrets (other than `GITHUB_TOKEN`) are not passed to workflows triggered from forks, and `GITHUB_TOKEN` is read-only there; avoid `pull_request_target` if you need to run PR code | https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows |
| Creating a PR review needs Pull requests: write; review events are APPROVE, REQUEST_CHANGES and COMMENT; `body` is required for COMMENT and REQUEST_CHANGES | https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request |
| "Allow GitHub Actions to create and approve pull requests" is off by default for new personal repos | https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository |
| Check-run write access via the REST API is only available to GitHub Apps; `GITHUB_TOKEN` is an installation token and can create check runs with `checks: write` (PATs cannot) | https://docs.github.com/en/rest/checks/runs · https://docs.github.com/en/actions/concepts/security/github_token |
| Check-run `conclusion` values: success, failure, neutral, cancelled, skipped, timed_out, action_required; `output.summary` and `output.text` max 65,535 characters; at most 50 annotations per request | https://docs.github.com/en/rest/checks/runs · OpenAPI spec |
| Review comment fields `line`, `side` (LEFT/RIGHT), `start_line`, `start_side`; `position` is being retired | https://docs.github.com/en/rest/pulls/comments · OpenAPI spec |
| ripgrep is **not** preinstalled on `ubuntu-24.04` runners; git and Python 3.12 are | https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md |
| `actions/checkout` current major is v7; `fetch-depth` defaults to 1, and 0 fetches all history | https://github.com/actions/checkout |
| Step summary max 1 MiB per step | https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands |

**Not documented** (to be confirmed on a test repo in Phase 5):
- the behaviour when a review comment's line is outside the diff (whether the whole review fails)
- the maximum number of comments per review
- the permission needed to remove a label from a PR
- `actions/setup-python`'s current major version

### LangGraph and LangChain

| Fact | Source |
|---|---|
| `create_react_agent` is deprecated: "moved to `langchain.agents`. Please update your import to `from langchain.agents import create_agent`" | Installed source `langgraph/prebuilt/chat_agent_executor.py:274-277`; https://docs.langchain.com/oss/python/migrate/langgraph-v1 |
| `create_agent(model, tools, *, system_prompt, middleware, response_format, checkpointer, …)`; the structured result is in `result["structured_response"]` | `langchain_v1/langchain/agents/factory.py` (langchain-ai/langchain); https://docs.langchain.com/oss/python/langchain/structured-output |
| `ProviderStrategy` uses the provider's native structured output (Anthropic `output_config.format`); automatic detection falls back to a regex that does **not** include `claude-sonnet-5` or `claude-opus-5`, while `langchain-anthropic`'s model profiles mark both `structured_output: True` | `factory.py:182-200, 577-625`; `langchain_anthropic/data/_profiles.py` |
| Middleware hooks: `before_agent`, `before_model`, `after_model`, `after_agent`, `wrap_model_call`, `wrap_tool_call`; `request.override(tools=…)`; `jump_to` end/tools/model | https://docs.langchain.com/oss/python/langchain/middleware/custom |
| `ModelCallLimitMiddleware(run_limit, exit_behavior="end")`; `ToolCallLimitMiddleware` | https://docs.langchain.com/oss/python/langchain/middleware/built-in |
| Jumping to `end` does **not** produce a `structured_response` | `factory.py` routing (`_make_model_to_tools_edge`, `_resolve_jump`) |
| A subgraph compiled with `checkpointer=None` inherits the parent's checkpointer | Installed source `langgraph/types.py:98-104`; https://docs.langchain.com/oss/python/langgraph/use-subgraphs |
| `interrupt()` needs a checkpointer and `thread_id`; resume with `Command(resume=…)`; the node re-runs from its start on resume; `__interrupt__` appears in stream updates | Installed source `langgraph/types.py:811` (docstring); https://docs.langchain.com/oss/python/langgraph/interrupts |
| `langgraph-checkpoint-postgres` (`AsyncPostgresSaver.from_conn_string`, `await setup()`) depends on psycopg ≥3.2 and psycopg-pool (not asyncpg) | https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres |
| `langchain` 1.4.2 requires `langgraph>=1.2.11,<1.3.0` and `langchain-core>=1.6.3` | PyPI / `libs/langchain_v1/pyproject.toml` |

### `langchain-anthropic` (1.7.4)

| Fact | Source |
|---|---|
| Requires `anthropic>=0.120.0,<2.0.0` and `langchain-core>=1.6.4,<2.0.0` | `libs/partners/anthropic/pyproject.toml` |
| Supports `thinking={"type":"adaptive"}`, `effort` (alias of `reasoning_effort`), `output_config` | `langchain_anthropic/chat_models.py` |
| `with_structured_output(method="json_schema")` uses native structured output; the default `function_calling` relies on forced tool use, which is rejected when thinking is enabled | `chat_models.py` (~2777-2816, ~1119) |
| Top-level automatic `cache_control` is supported | `chat_models.py` docstring |
| `usage_metadata.input_token_details` has `cache_read` and `cache_creation`; `input_tokens` includes them | `chat_models.py` (~3258-3290) |
| Thinking blocks (with signatures) are passed back automatically in multi-turn tool use | `chat_models.py` message conversion |

### Anthropic models and prices

From the `claude-api` skill reference (cached 2026-06-24), per million tokens:
`claude-sonnet-5` $2 / $10, `claude-opus-5` $5 / $25, `claude-opus-5-5` $4 / $20,
`claude-fable-5-1` $10 / $50, `claude-haiku-4-5` $1 / $5. Adaptive thinking is on by
default for Sonnet 5 and Opus 5; `budget_tokens` is rejected on both.

### Repository

`https://github.com/Badri-Narayanan/CodeSage` redirects to **`Badri-Rajendran/CodeSage`**
(public, default branch `main`), checked with the GitHub REST API. The local `origin` was
updated to the new URL in Phase 0.

## Phase 1 spike results (2026-09-24)

Run in a throwaway venv with langchain 1.4.2, langchain-anthropic 1.7.4, langchain-core 1.6.5,
langgraph 1.2.12, langgraph-checkpoint-postgres 3.1.2 and psycopg 3.3.6. The owner approved
one paid run (the forced-finish case), which cost **$0.0115**.

| # | Item | Result |
|---|---|---|
| 1 | `create_agent` + `ChatAnthropic("claude-sonnet-5")` + tools + adaptive thinking + `ProviderStrategy(Findings)` | **Pass (partly).** A call with tools bound, thinking on and `output_config.format` set made a tool call without error. The next call, carrying the thinking, tool-use and tool-result history, returned a valid `Findings` with the correct path and line. A full, unforced multi-step run wasn't paid for; Phase 4's manual run covers it. |
| 2 | Budget middleware strips tools (`request.override(tools=[])` plus a "finish now" message) → structured final answer | **Pass.** 2 calls: a tool call, then `end_turn` with a valid `structured_response`. The recipe is `awrap_model_call`: price `resp.result`'s `AIMessage.usage_metadata` after `handler()`; once over the allowance, override `tools=[]` before calling it. |
| 3 | `usage_metadata` token fields | **Pass, with a caveat.** `input_tokens` includes cached tokens. `input_token_details` has `cache_read`, `cache_creation`, `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`. When the TTL keys are present, langchain sets `cache_creation` to **0** and the writes are in the TTL keys, so pricing must use `cache_creation or (5m + 1h)`. 1h writes cost 2× input, 5m writes cost 1.25×. `output_token_details.reasoning` is also reported. Removing the tools changes the cached prefix, so the forced final call doesn't hit the cache. |
| 4 | Refusals | **Answered from source:** `ChatAnthropic` doesn't raise. `stop_reason` is in `response_metadata`, and `ProviderStrategy` parsing of a refusal raises `StructuredOutputValidationError`. The reviewer node checks `stop_reason == "refusal"` and raises a clear error. |
| 5 | `AsyncPostgresSaver` + `interrupt()` + `Command(resume=…)` across processes | **Pass** (pgvector container on port 5544). Process A stopped at the interrupt, and `__interrupt__` appeared in the `updates` stream with the payload. Process B resumed: the gate node re-ran from its start, then `publish` ran. Process C resumed the finished thread and got no updates; nothing re-ran. |

**Dependency note:** unpinned, `langchain-anthropic` resolves `anthropic` 1.8.0 (1.x is out). The
approved pin `anthropic>=0.120,<1` resolves 0.125.0, which satisfies langchain-anthropic, so
the 1.x SDK upgrade stays out of scope for this round.

## Open items for the Phase 1 spike (original list)


These have to pass against the live API before Phase 3 starts. If one fails, the
owner chooses the fallback.

1. `create_agent` + `ChatAnthropic("claude-sonnet-5")` + tools + adaptive thinking +
   `ProviderStrategy(Findings)` returns a valid `structured_response`.
   **Fallback:** `ToolStrategy` with a final `submit_findings` tool, or a separate structured call after the loop.
2. `BudgetMiddleware` removing the tools in `wrap_model_call` produces a structured
   final answer on the next call. **Fallback:** end the loop, then make one
   structured "summarize your findings" call with the transcript.
3. `usage_metadata` gives the token fields listed above, so cost can be computed per call.
4. How `ChatAnthropic` surfaces `stop_reason == "refusal"`.
5. `AsyncPostgresSaver` + `interrupt()` + `Command(resume=…)` survives a process
   restart with the upgraded `langgraph`.
