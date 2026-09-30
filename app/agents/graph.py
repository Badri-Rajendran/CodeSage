"""LangGraph orchestration for a single PR review.

    START ─┬─▶ security ────┐
           ├─▶ correctness ─┼─▶ reflection ─▶ judge ─┬───────────▶ human_gate ─▶ publish ─▶ END
           └─▶ style ───────┘                        │ score low      ▲
            (parallel agents)                         └─▶ revise ─▶ judge (at most once)

- Reviewers are tool-using agents (``app.agents.reviewers``); reflection returns
  decisions that code applies (``app.agents.reflection``); the judge scores the
  result (``app.eval.judge``). A low score triggers at most one ``revise`` round,
  if the budget allows.
- ``human_gate`` computes why the review needs attention (``gate_reasons``). What
  it does next depends on the mode:
    report (default: MCP, eval, tests)   records the reasons; never pauses
    action (GitHub Action)               same; the check run carries the verdict
    local  (FastAPI jobs)                ``interrupt()`` until a human decides
- ``publish`` hands the final state to ``deps.publisher`` when one is set.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.deps import EngineDeps, GraphMode, Publisher
from app.agents.reflection import make_reflection_node
from app.agents.reviewers import REVIEWER_ROLES, make_reviewer_node
from app.agents.state import ReviewState
from app.config import Settings, get_settings
from app.diff import Diff, parse_diff
from app.eval.judge import LLMJudge
from app.llm.budget import BudgetGuard
from app.llm.client import LLMClient
from app.review_config import ReviewConfig, default_review_config
from app.workspace import Workspace

# Node order as the console shows it. Mirrored in ReviewService.STAGE_ORDER and
# web/src/lib (useReviewStream.ts, format.ts, types.ts), StageTimeline.tsx.
STAGES = (*REVIEWER_ROLES, "reflection", "judge", "revise", "human_gate", "publish")


def compute_gate_reasons(state: ReviewState, cfg: ReviewConfig) -> list[str]:
    """Why this review needs a human's attention. Pure: safe to re-run on resume."""
    reasons = [
        f"{f['severity']} finding: {f['title']}"
        for f in state.get("findings", [])
        if f.get("severity") in cfg.fail_on
    ]
    score = state.get("judge_score")
    if score is None:
        reasons.append("budget-limited: not judged")
    elif score < cfg.gate_threshold:
        reasons.append(f"judge score {score:.2f} < {cfg.gate_threshold:.2f}")
    return reasons


def build_review_graph(deps: EngineDeps, *, mode: GraphMode = "report", checkpointer=None):
    """Compile the review graph. ``local`` mode requires a checkpointer."""
    if mode == "local" and checkpointer is None:
        raise ValueError("local mode pauses at the gate and needs a checkpointer")
    judge = LLMJudge(deps.client, deps.cfg, deps.budget, deps.settings)

    async def judge_node(state: ReviewState) -> dict:
        if not deps.stubbed and not deps.budget.can_start("judge"):
            deps.budget.mark_limited("judge")
            return {"judge_score": None, "judge_dimensions": {}, "budget_limited": True,
                    "judge_rationale": "Not judged: the review budget was reached."}
        out = await judge.score(
            diff=deps.workspace.diff.render(),
            findings=state.get("findings", []),
            summary=state.get("summary", ""),
        )
        return {
            "judge_score": out["score"],
            "judge_rationale": out.get("rationale", ""),
            "judge_dimensions": out.get("dimensions", {}),
        }

    def after_judge(state: ReviewState) -> str:
        score = state.get("judge_score")
        if (
            score is not None
            and score < deps.cfg.gate_threshold
            and state.get("revision_count", 0) == 0
            and not deps.stubbed
            and deps.budget.can_start("revise")
        ):
            return "revise"
        return "human_gate"

    def human_gate_node(state: ReviewState) -> dict:
        reasons = compute_gate_reasons(state, deps.cfg)
        update = {
            "gate_tripped": bool(reasons),
            "gate_reasons": reasons,
            "requires_human_approval": bool(reasons),
        }
        if mode != "local" or not reasons:
            return update
        # Pauses the graph; on resume this node re-runs from the top and
        # interrupt() returns the decision, e.g. {"approved": True, "note": "..."}.
        decision = interrupt({"reasons": reasons, "judge_score": state.get("judge_score")})
        return {
            **update,
            "decision": "approved" if decision.get("approved") else "rejected",
            "decision_note": decision.get("note"),
        }

    async def publish_node(state: ReviewState) -> dict:
        if deps.publisher is None:
            return {}
        return await deps.publisher(dict(state)) or {}

    graph = StateGraph(ReviewState)
    for role in REVIEWER_ROLES:
        graph.add_node(role, make_reviewer_node(role, deps))
        graph.add_edge(START, role)
        graph.add_edge(role, "reflection")  # reflection waits for all three
    graph.add_node("reflection", make_reflection_node(deps))
    graph.add_node("judge", judge_node)
    graph.add_node("revise", make_reflection_node(deps, revise=True))
    graph.add_node("human_gate", human_gate_node)
    graph.add_node("publish", publish_node)

    graph.add_edge("reflection", "judge")
    graph.add_conditional_edges("judge", after_judge, ["revise", "human_gate"])
    graph.add_edge("revise", "judge")
    graph.add_edge("human_gate", "publish")
    graph.add_edge("publish", END)
    return graph.compile(checkpointer=checkpointer)


def prepare_diff(text: str, cfg: ReviewConfig) -> Diff:
    """Parse the PR diff, drop ignored paths, and keep at most ``max_files`` files."""
    kept, _skipped = parse_diff(text).filter(list(cfg.ignore_paths)).limit(cfg.max_files)
    return kept


def make_deps(
    client: LLMClient,
    workspace: Workspace,
    settings: Settings | None = None,
    *,
    cfg: ReviewConfig | None = None,
    publisher: Publisher | None = None,
) -> EngineDeps:
    settings = settings or get_settings()
    cfg = cfg or default_review_config(settings)
    return EngineDeps(
        client=client,
        workspace=workspace,
        settings=settings,
        cfg=cfg,
        budget=BudgetGuard(cfg.budget_usd, tracker=client.tracker),
        publisher=publisher,
    )
