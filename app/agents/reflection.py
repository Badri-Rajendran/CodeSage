"""Reflection: a reviewer-of-reviewers that returns *decisions*, not rewrites.

The agent reads every draft finding (each with an ``id`` and ``reviewer``),
may check claims with read-only tools, and returns keep / drop / merge
decisions plus a summary. ``apply_decisions`` applies them in plain code, so a
finding's attribution can't be lost by the model (the earlier single-call
rewrite dropped ``reviewer``).

The same node, given the judge's feedback, is the ``revise`` round.
"""

from __future__ import annotations

import json

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage

from app.agents.deps import EngineDeps
from app.agents.findings import anchor_and_sort
from app.agents.middleware import AgentStopped, FinishMiddleware, TraceMiddleware
from app.agents.react import ReactTrace
from app.agents.reviewers.base import diff_for_prompt
from app.agents.schemas import SEVERITY_RANK, Decision, ReflectionDecisions
from app.agents.state import ReviewState
from app.diff import Diff
from app.llm import stub
from app.llm.models import build_chat_model
from app.llm.prompts import DECISION_REFLECTION_SYSTEM, reflection_input

MAX_MODEL_CALLS = 4
_OVERRIDES = ("severity", "title", "rationale", "suggestion", "line", "end_line")


def apply_decisions(drafts: list[dict], decisions: list[Decision], diff: Diff) -> list[dict]:
    """Apply reflection decisions to findings; unmentioned findings are kept."""
    by_id = {f["id"]: dict(f) for f in drafts}
    # Drops are resolved first, so "merge A into B" followed by "drop B" keeps A
    # (the merge is unusable) instead of losing both.
    removed: set[str] = {d.finding_id for d in decisions if d.action == "drop"}
    for d in decisions:
        f = by_id.get(d.finding_id)
        if f is None or d.finding_id in removed:
            continue
        target = by_id.get(d.merge_into or "")
        if d.action == "merge" and target is not None and d.merge_into not in removed \
                and d.merge_into != d.finding_id:
            target["evidence"] = list(dict.fromkeys([*target["evidence"], *f["evidence"]]))
            if SEVERITY_RANK[f["severity"]] > SEVERITY_RANK[target["severity"]]:
                target["severity"] = f["severity"]
            target["confidence"] = max(target["confidence"], f["confidence"])
            target["merged_from"] = list(dict.fromkeys(
                [*target["merged_from"], f["reviewer"], *f["merged_from"]]
            ))
            removed.add(d.finding_id)
            continue
        for key in _OVERRIDES:  # "keep" (or an unusable merge): apply corrections
            value = getattr(d, key)
            if value is not None:
                f[key] = value
    kept = [f for fid, f in by_id.items() if fid not in removed]
    return anchor_and_sort(kept, diff)


def make_reflection_node(deps: EngineDeps, *, revise: bool = False):
    stage = "revise" if revise else "reflection"

    async def node(state: ReviewState) -> dict:
        source = state.get("findings", []) if revise else state.get("draft_findings", [])
        update: dict = {"revision_count": state.get("revision_count", 0) + 1} if revise else {}
        if deps.stubbed:
            return {**update, **_stub_reflect(source, state, deps, stage)}
        if not source:
            return {**update, "findings": [], "reflection_trace": None,
                    "summary": "No issues found: the reviewers reported nothing for this change."}
        if not deps.budget.can_start(stage):
            deps.budget.mark_limited(stage)
            return {**update, "findings": anchor_and_sort(source, deps.workspace.diff),
                    "summary": state.get("summary") or "Reflection skipped: budget reached.",
                    "budget_limited": True}
        feedback = None
        if revise:
            dims = json.dumps(state.get("judge_dimensions", {}))
            feedback = f"{state.get('judge_rationale', '')}\nDimension scores: {dims}"
        decisions, trace = await _agent_reflect(stage, source, feedback, deps)
        return {
            **update,
            "findings": apply_decisions(source, decisions.decisions, deps.workspace.diff),
            "summary": decisions.summary,
            "reflection_trace": trace,
            "budget_limited": stage in deps.budget.limited,
        }

    node.__name__ = f"{stage}_node"
    return node


async def _agent_reflect(
    stage: str, findings: list[dict], feedback: str | None, deps: EngineDeps
) -> tuple[ReflectionDecisions, dict]:
    trace = ReactTrace(agent=stage)
    finish = FinishMiddleware(stage, deps.cfg.reviewer_model, deps.budget, MAX_MODEL_CALLS)
    agent = create_agent(
        build_chat_model("reflection", deps.cfg, deps.settings),
        deps.workspace.tools_for(stage, read_only=True),
        system_prompt=DECISION_REFLECTION_SYSTEM,
        response_format=ProviderStrategy(ReflectionDecisions),
        middleware=[
            finish,
            TraceMiddleware(trace),
            ModelCallLimitMiddleware(  # type: ignore[list-item]  # library generic variance
                run_limit=MAX_MODEL_CALLS, exit_behavior="error"
            ),
        ],
        name=f"{stage}_agent",
    )
    visible = [
        {k: f.get(k) for k in ("id", "reviewer", "severity", "confidence", "path", "line",
                                "title", "rationale", "suggestion", "evidence")}
        for f in findings
    ]
    prompt = reflection_input(
        diff=diff_for_prompt(deps),
        findings_json=json.dumps(visible, indent=1),
        judge_feedback=feedback,
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(prompt)]}, {"recursion_limit": 2 * MAX_MODEL_CALLS + 6}
    )
    response = result.get("structured_response")
    if not isinstance(response, ReflectionDecisions):
        raise AgentStopped(f"{stage}: the agent ended without structured decisions")
    for d in response.decisions:
        if d.action != "keep":
            target = f" into {d.merge_into}" if d.action == "merge" else ""
            trace.thought(f"{d.action} {d.finding_id}{target}: {d.reason[:300]}")
    return response, trace.as_dict()


def _stub_reflect(source: list[dict], state: ReviewState, deps: EngineDeps, stage: str) -> dict:
    out = stub.generate("reflection", {"diff": state["diff"], "findings": source})
    deps.tracker.record(stage, f"{deps.cfg.reviewer_model} (stub)")
    return {
        "findings": anchor_and_sort([dict(f) for f in out["findings"]], deps.workspace.diff),
        "summary": out["summary"],
        "reflection_trace": None,
    }
