"""LangGraph orchestration for a single PR review.

Topology:

    START ──┬──▶ security ────┐
            ├──▶ correctness ─┼──▶ reflection ──▶ judge ──▶ human_gate ──▶ END
            └──▶ style ───────┘
            (parallel)          (consolidate +     (LLM-as-   (HITL flag)
                                 self-reflection)   Judge)

The three reviewers fan out in parallel; reflection joins them. The human_gate
node sets `requires_human_approval` when the judge score falls below the configured
threshold — the API then blocks auto-application of the review until a human
approves via POST /reviews/{id}/approve. (Swap the gate for LangGraph `interrupt()`
+ a Postgres checkpointer to get true in-graph pause/resume.)
"""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph

from app.agents.reflection import ReflectionAgent
from app.agents.reviewers import (
    CorrectnessReviewer,
    SecurityReviewer,
    StyleReviewer,
)
from app.agents.state import ReviewState
from app.agents.tools import ReviewTools
from app.config import Settings, get_settings
from app.eval.judge import LLMJudge
from app.llm.client import LLMClient


@dataclass
class GraphDeps:
    client: LLMClient
    tools: ReviewTools
    reflection: ReflectionAgent
    judge: LLMJudge
    settings: Settings


def build_review_graph(deps: GraphDeps):
    """Compile the review graph with bound dependencies."""

    security = SecurityReviewer(deps.client)
    correctness = CorrectnessReviewer(deps.client)
    style = StyleReviewer(deps.client)

    def _reviewer_node(agent):
        async def node(state: ReviewState) -> dict:
            out = await agent.review(
                repo=state["repo"],
                pr_number=state.get("pr_number"),
                diff=state["diff"],
                tools=deps.tools,
            )
            return {"draft_findings": out["findings"], "traces": [out["trace"]]}

        return node

    async def reflection_node(state: ReviewState) -> dict:
        out = await deps.reflection.reflect(
            diff=state["diff"], draft_findings=state.get("draft_findings", [])
        )
        return {"findings": out["findings"], "summary": out["summary"]}

    async def judge_node(state: ReviewState) -> dict:
        out = await deps.judge.score(
            diff=state["diff"],
            findings=state.get("findings", []),
            summary=state.get("summary", ""),
        )
        return {
            "judge_score": out["score"],
            "judge_rationale": out.get("rationale", ""),
            "judge_dimensions": out.get("dimensions", {}),
        }

    def human_gate_node(state: ReviewState) -> dict:
        score = state.get("judge_score", 0.0)
        has_critical = any(
            f.get("severity") in ("critical", "high") for f in state.get("findings", [])
        )
        requires = score < deps.settings.hitl_threshold or has_critical
        return {"requires_human_approval": requires, "approved": None}

    graph = StateGraph(ReviewState)
    graph.add_node("security", _reviewer_node(security))
    graph.add_node("correctness", _reviewer_node(correctness))
    graph.add_node("style", _reviewer_node(style))
    graph.add_node("reflection", reflection_node)
    graph.add_node("judge", judge_node)
    graph.add_node("human_gate", human_gate_node)

    # Fan out to the parallel reviewers.
    graph.add_edge(START, "security")
    graph.add_edge(START, "correctness")
    graph.add_edge(START, "style")

    # Join at reflection (waits for all three reviewers).
    graph.add_edge("security", "reflection")
    graph.add_edge("correctness", "reflection")
    graph.add_edge("style", "reflection")

    graph.add_edge("reflection", "judge")
    graph.add_edge("judge", "human_gate")
    graph.add_edge("human_gate", END)

    return graph.compile()


def make_deps(
    client: LLMClient,
    tools: ReviewTools,
    settings: Settings | None = None,
) -> GraphDeps:
    settings = settings or get_settings()
    return GraphDeps(
        client=client,
        tools=tools,
        reflection=ReflectionAgent(client),
        judge=LLMJudge(client),
        settings=settings,
    )
