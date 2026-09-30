# Design 01: Review Engine (agents, tools, budget, reflection, judge)

> Status: **implemented** (designed 2026-09-23). The two points marked ⚠︎ were
> proven against the live API in the Phase 1 spike; see
> [../decisions.md](../decisions.md#phase-1-spike-results-2026-09-24).

## Goals

- Reviewers that are **actually agentic**: Claude decides what to read, search and
  test, and the trace records real tool calls rather than scripted strings.
- Findings that are **anchored** to diff lines and **attributed** to the reviewer
  that raised them, all the way to publication.
- A **hard $0.50 budget** per review that still ends in a structured result.
- One engine shared by the GitHub Action and local mode, with the existing
  offline stub mode kept so the current tests pass without a key.

**Non-goals** (deferred or out of scope): per-reviewer failure isolation, typed
retry policies, large-diff chunking, prompt-injection hardening, and new
automated tests.

## Framework

Reviewers use **LangChain v1 `create_agent`** (`from langchain.agents import create_agent`)
with **`ChatAnthropic`** from `langchain-anthropic`. `create_agent` is the documented
successor to `langgraph.prebuilt.create_react_agent`, which LangGraph 1.x deprecates.
Each agent is a compiled LangGraph graph, run from a wrapper node in the outer
review graph.

## Models (`app/llm/models.py`)

| Role | Default model | Effort | Notes |
|---|---|---|---|
| Reviewer (×3) | `claude-sonnet-5` | `medium` | Adaptive thinking (`thinking={"type": "adaptive"}`) |
| Reflection / revise | `claude-sonnet-5` | `medium` | Same as reviewers |
| Judge | `claude-opus-5` | `high` | Different model from the reviewers, so it isn't grading its own work |

`build_chat_model(role, cfg) -> ChatAnthropic` reads settings (`CODESAGE_MODEL`,
`CODESAGE_JUDGE_MODEL`, `CODESAGE_EFFORT`, plus new `CODESAGE_REVIEWER_EFFORT` and
`CODESAGE_JUDGE_EFFORT`), overridden by `.codesage.yml`. Every model is built with
top-level automatic prompt caching (`cache_control={"type": "ephemeral"}`), so each
agent's repeated system prompt, tools and diff prefix is cached across its turns.

**Model-profile caveat (verified).** LangChain's automatic structured-output
strategy detection doesn't match `claude-sonnet-5` or `claude-opus-5` by name.
So we always pass a `ChatAnthropic` instance and set `ProviderStrategy`
explicitly, and we never pass a bare model string.

## Workspace tools (`app/workspace/`)

`Workspace(root: Path, base_sha: str, head_sha: str, test_cfg: TestConfig | None, semantic: SemanticSearch | None)`

| Tool | Signature | Behavior and limits |
|---|---|---|
| `read_file` | `(path, start_line=1, end_line=None)` | Returns numbered lines; max 400 lines or 40 KB per call; confined to `root`; `.git/` is refused |
| `search_code` | `(pattern, path_glob=None, fixed=False)` | `git grep -n -I` at the head commit; max 50 matches; `-F` when `fixed`. ripgrep isn't on the runners, and git is always present |
| `list_dir` | `(path=".")` | Tracked files only (`git ls-files`), max 200 entries |
| `git_log` | `(path, max_count=10)` | `git log --oneline` for a path |
| `git_blame` | `(path, start_line, end_line)` | Range capped at 200 lines |
| `get_diff` | `(path=None)` | The PR diff for one file, or the file list with change counts |
| `run_tests` | `(target=None)` | Correctness reviewer only, and only when `tests.command` is configured (Action mode); see below |
| `semantic_search` | `(query, top_k=6)` | Local mode only; wraps `VectorStore.search` (`app/db/vector_store.py`) |

- **Path confinement:** every path is resolved and must satisfy `is_relative_to(root)`.
  Symlinks are resolved before the check, as in `resolve_ingest_root` (`app/rag/pipeline.py`).
- **Output cap:** every tool result is capped at 12,000 characters. Truncated
  results say so, so the model can narrow its request.
- **Tool errors** come back as a string result (`"error: …"`), not an exception, so
  the agent can recover.

### `run_tests`

- Runs `tests.setup` once, on first use (for example `pip install -e .[dev]` or `npm ci`), then `tests.command`
  with `{target}` substituted if the model gave a target (for example a test file path).
- Runs in a subprocess with `start_new_session=True`. On timeout (default 300 s) the whole
  process group is killed; this reuses the kill logic in `app/agents/sandbox.py`.
- **The environment is scrubbed:** `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `ACTIONS_*`,
  `CODESAGE_*` and every variable whose name contains `TOKEN`, `KEY` or `SECRET` are
  removed before running.
- Returns the exit code plus the last 4,000 characters of combined output.
- Not offered in local mode (only the runner is a disposable VM), and not offered
  when `tests.command` is unset.

## Diff model (`app/diff/`)

`parse_diff(text) -> Diff` with `Diff.files: list[FileDiff]`, and each `FileDiff` has
`path`, `status` (added, modified, deleted or renamed), `hunks: list[Hunk]` and
`right_ranges: list[tuple[int, int]]`. `Diff.is_commentable(path, line)` is true when
`line` falls inside a RIGHT-side hunk range, meaning an added or context line. This
is what GitHub's `line` + `side: RIGHT` review-comment fields address.
`Diff.filter(ignore_globs)` drops ignored paths. No new dependency is needed: hunk
headers are parsed with a regex (`@@ -a,b +c,d @@`).

## Findings schema

A Pydantic model, used as `ProviderStrategy(Findings)`:

```python
class Finding(BaseModel):
    id: str                 # assigned by code after the agent returns ("sec-1", …)
    reviewer: Literal["security", "correctness", "style"]  # set by code, not the model
    category: str           # e.g. "injection", "null-handling", "naming"
    severity: Literal["info", "low", "medium", "high", "critical"]
    confidence: float       # 0..1
    path: str | None
    line: int | None        # RIGHT-side line in the PR head
    end_line: int | None
    title: str
    rationale: str
    suggestion: str | None
    evidence: list[str]     # e.g. "read_file app/x.py:40-60", "run_tests: 2 failed"
    merged_from: list[str] = []  # set by apply_decisions(); reviewers of merged-in findings

class Findings(BaseModel):
    findings: list[FindingDraft]   # FindingDraft = Finding without id/reviewer
    notes: str                     # anything the reviewer couldn't anchor or check
```

The model produces `FindingDraft`s. Code assigns `id` and `reviewer`. This is the
first half of the attribution fix: reflection never gets a chance to drop the field.

## Reviewer agents

For each role (security, correctness, style):

```python
create_agent(
    model=build_chat_model("reviewer", cfg),
    tools=workspace.tools_for(role),          # run_tests only for correctness
    system_prompt=REVIEWER_PROMPTS[role],
    response_format=ProviderStrategy(Findings),
    middleware=[budget_mw, ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"), trace_mw],
)
```

- **Input message:** the PR title and body, the file list with change counts, and the diff,
  all inside clearly labelled data sections. Then the instruction to investigate
  with tools and return `Findings`.
- **System prompts** (rewritten in `app/llm/prompts.py`) give the role's focus
  (kept from today's `SECURITY_SYSTEM`, `CORRECTNESS_SYSTEM` and `STYLE_SYSTEM`) and
  the rules:
  - use tools to confirm before reporting
  - anchor findings to RIGHT-side diff lines
  - put the supporting tool results in `evidence`
  - report nothing rather than guess
- **Wrapper node** `reviewer_node(role)`: builds the agent, invokes it with
  `recursion_limit` sized to the call limit, reads `result["structured_response"]`,
  assigns ids and the reviewer, and returns
  `{"draft_findings": [...], "traces": [trace]}`. The outer graph's additive reducers
  merge the three results in parallel, as they do today.
- **Subgraph checkpointing:** the agent is compiled with `checkpointer=None`, so it
  inherits the parent's checkpointer in local mode (verified: `langgraph/types.py:98-104`).

### Trace middleware

`TraceMiddleware` builds a `ReactTrace` (`app/agents/react.py`, unchanged) from the
real agent loop:

- `after_model`: each text block becomes a `thought` step; each tool call becomes an
  `action` step, with the tool name and a short JSON summary of the arguments.
- `wrap_tool_call`: each tool result becomes an `observation` step, trimmed to 500
  characters, with the tool name.

The console already renders this shape (`web/src/components/TraceView.tsx`), so no UI
change is needed. Thinking blocks aren't shown in the trace: they're `omitted` by
default on Claude 5 models.

## Budget

`BudgetGuard(total_usd=0.50, allowances={"security": .10, "correctness": .10, "style": .10}, reserve_usd=.20)`

- **Pricing:** every model call is priced from `AIMessage.usage_metadata`, using
  `input_tokens`, `output_tokens` and `input_token_details.cache_read` /
  `cache_creation`, through `cost_usd()` in `app/llm/telemetry.py`. The same numbers
  go into the review's `CostTracker`, which keeps the telemetry, the console cost
  view and `usage_events` working.
- **Price table:** `PRICES` gains `claude-sonnet-5` (2.00 / 10.00), `claude-opus-5`
  (5.00 / 25.00), `claude-opus-5-5` (4.00 / 20.00) and `claude-fable-5-1`
  (10.00 / 50.00), per million tokens.
- **The finish-now rule** ⚠︎: `BudgetMiddleware` holds one agent's allowance.
  - After each model call (`after_model`) it records the cost.
  - Before the next call (`wrap_model_call`), if the agent's spend is at or above
    its allowance minus the finalize margin ($0.03), it calls
    `handler(request.override(tools=[]))` and appends a "budget reached: stop
    investigating and return your findings now" instruction.
  - With no tools, the model must answer, and `ProviderStrategy` makes that answer
    a `Findings` object.
  - The agent is then marked `budget_limited`.
- **Why this has to be proven:** jumping to `end` ends the agent *without* a
  structured result (verified in `create_agent`'s routing), so we can't simply stop
  it. Removing the tools and asking it to finish is the design. The spike must show
  that it reliably yields `structured_response`.
- **Hard stop:** no new model call starts once `BudgetGuard.total_spent >= total_usd`.
  The limit is enforced *between* calls, so a single in-flight call can overshoot
  slightly. That overshoot is bounded by per-call `max_tokens` (4,000 for every role; the
  judge was raised from the planned 3,000 because adaptive thinking counts toward it),
  and the actual total is always reported.
- **The reserve** ($0.20) is used only by reflection, the judge and a revise round. The
  reviewers can't touch it.
- **If the reserve runs out:** reflection or the judge is skipped. The review is
  marked `budget_limited`, and the Action's check run reports `neutral` when it has
  no judge score.

## Reflection (decision-based)

`ReflectionAgent`: `create_agent` on `claude-sonnet-5`, read-only tools
(`read_file`, `search_code`, `get_diff`), `ModelCallLimitMiddleware(run_limit=4)`,
and `ProviderStrategy(ReflectionDecisions)`:

```python
class Decision(BaseModel):
    finding_id: str
    action: Literal["keep", "drop", "merge"]
    merge_into: str | None       # required when action == "merge"
    severity: Severity | None    # corrected severity, if any
    title: str | None
    rationale: str | None
    suggestion: str | None
    line: int | None
    end_line: int | None
    reason: str                  # why: shown in the trace and used for audit

class ReflectionDecisions(BaseModel):
    decisions: list[Decision]
    summary: str                 # one-paragraph review summary
```

`apply_decisions(drafts, decisions, diff) -> findings` (plain code, no LLM):

- Findings without a decision are **kept** unchanged.
- `merge` folds the source into the target: evidence is combined and the higher
  severity wins. The source's `reviewer` goes into a `merged_from` list, so
  attribution survives.
- Findings whose `(path, line)` isn't commentable per the diff model have `line`
  cleared; they're published in the review body instead of inline.
- The result is sorted by severity, then confidence.

This replaces today's single rewrite call (`app/agents/reflection.py`), which
dropped `reviewer` with a real API key because the reflection schema had no field for it.

## Judge

`LLMJudge.score(diff, findings, summary)`: one call to
`ChatAnthropic("claude-opus-5").with_structured_output(JudgeResult, method="json_schema")`.
No tools. It keeps today's four dimensions (correctness, groundedness, actionability,
signal_to_noise), the overall `score` and the `rationale` (the `JUDGE_SYSTEM`
contract in `app/llm/prompts.py`). The score is clamped to [0, 1], as today.
`json_schema` is chosen because forced tool use, the default `function_calling`
method, is rejected when thinking is on (verified in langchain-anthropic).

## Revision round

Conditional edge after `judge`:

```
judge → revise   if score < gate.threshold and revision_count == 0
                 and budget.remaining >= 0.08
judge → human_gate otherwise
revise → judge
```

`revise` runs the reflection agent again, on the current findings, with the judge's
rationale and dimensions as extra input. It returns decisions, and `revision_count`
becomes 1. It never runs twice.

## Gate

`gate_reasons` lists every reason that applies: `"critical/high finding: <title>"`,
`"judge score 0.52 < 0.60"`, `"budget-limited: not judged"`. The gate trips when
the list is non-empty. What happens next depends on the mode (see designs 02 and 03).

## Stub mode (no API key)

`LLMClient.stubbed` (`app/llm/client.py`) stays the switch. When stubbed:

- Reviewer nodes call `stub.generate(role, {"diff": diff})` and emit a scripted
  trace with one `thought` and one `action` step. `tests/test_review_graph.py`
  asserts that both kinds are present.
- Reflection uses `stub.generate("reflection", …)` and the judge uses `stub.generate("judge", …)`,
  both unchanged. Stub findings get ids and reviewers from code, as live ones do.
- No agents, budget middleware or network calls.

Stub output keeps today's shapes, so all current assertions in
`tests/test_review_graph.py` and `tests/test_stub.py` still hold.

## What happens to existing modules

| Module | Change |
|---|---|
| `app/agents/reviewers/base.py` | The scripted `review()` is replaced by the wrapper-node builder |
| `app/agents/tools.py` | `ReviewTools` and the unused `TOOL_SCHEMAS` are replaced by `app/workspace/` tools |
| `app/agents/sandbox.py` | Kept for its process-group timeout helper; the "added lines only" runner is no longer used by agents |
| `app/llm/client.py` | Kept for stub branching and any remaining structured calls; the live path moves to `ChatAnthropic`. The `anthropic` SDK goes from 0.109 to ≥0.120 (a requirement of langchain-anthropic); `_call_model` is re-checked in Phase 2 |
| `app/llm/prompts.py` | Reviewer and reflection prompts rewritten for tool use; `FINDINGS_SCHEMA` and `REFLECTION_SCHEMA` replaced by the Pydantic models above |
| `app/eval/harness.py` | Untouched in this round (eval is deferred); it must still import and run in stub mode |

## Error behavior (minimal, by decision)

The robustness bundle is out of scope. The engine's error behavior is:

- A model or tool exception in any node fails the review. In the Action that means
  a `CodeSage error` check run and exit code 1; locally it means `review.failed`.
- `ChatAnthropic`'s default SDK retries (429, 5xx, connection errors) apply.
- A refusal or a missing `structured_response` from a reviewer is treated as an
  error, with a clear message. Whether `ChatAnthropic` raises on
  `stop_reason == "refusal"` is to be confirmed in the spike.
