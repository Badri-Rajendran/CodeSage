"""Reviewer nodes: one tool-using agent per role (security, correctness, style).

Live mode: a LangChain ``create_agent`` loop over the workspace tools. Claude
decides what to read, search or test, and must finish with a structured
``Findings`` object (``ProviderStrategy``). Middleware meters the budget, forces
a structured finish when the agent runs low or reaches its last step, and
records the real tool calls as the agent's trace.

Stub mode (no API key): the deterministic heuristics in ``app.llm.stub`` with a
short scripted trace, so tests and CI run offline.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage

from app.agents.deps import EngineDeps
from app.agents.findings import attribute
from app.agents.middleware import AgentStopped, FinishMiddleware, TraceMiddleware
from app.agents.react import ReactTrace
from app.agents.schemas import Findings
from app.agents.state import ReviewState
from app.llm import stub
from app.llm.models import build_chat_model
from app.llm.prompts import review_input, reviewer_system

REVIEWER_ROLES = ("security", "correctness", "style")
MAX_MODEL_CALLS = 8
# Beyond this the agent reads the rest of the diff per file with get_diff.
MAX_DIFF_CHARS = 150_000


def diff_for_prompt(deps: EngineDeps) -> str:
    text = deps.workspace.diff.render()
    if len(text) <= MAX_DIFF_CHARS:
        return text
    return (
        text[:MAX_DIFF_CHARS]
        + "\n... [diff truncated; use get_diff(path) for the remaining files]\n"
    )


def make_reviewer_node(role: str, deps: EngineDeps):
    async def node(state: ReviewState) -> dict:
        if deps.stubbed:
            return _stub_review(role, state, deps)
        return await _agent_review(role, state, deps)

    node.__name__ = f"{role}_node"
    return node


async def _agent_review(role: str, state: ReviewState, deps: EngineDeps) -> dict:
    trace = ReactTrace(agent=role)
    tools = deps.workspace.tools_for(role)
    finish = FinishMiddleware(role, deps.cfg.reviewer_model, deps.budget, MAX_MODEL_CALLS)
    agent = create_agent(
        build_chat_model("reviewer", deps.cfg, deps.settings),
        tools,
        system_prompt=reviewer_system(
            role, can_run_tests=any(t.name == "run_tests" for t in tools)
        ),
        response_format=ProviderStrategy(Findings),
        middleware=[
            finish,
            TraceMiddleware(trace),
            ModelCallLimitMiddleware(  # type: ignore[list-item]  # library generic variance
                run_limit=MAX_MODEL_CALLS, exit_behavior="error"
            ),
        ],
        name=f"{role}_reviewer",
    )
    pr = state.get("pr") or {}
    prompt = review_input(
        title=pr.get("title", ""),
        body=pr.get("body", ""),
        file_summary=deps.workspace.diff.summary(),
        diff=diff_for_prompt(deps),
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(prompt)]},
        {"recursion_limit": 2 * MAX_MODEL_CALLS + 6},
    )
    response = result.get("structured_response")
    if not isinstance(response, Findings):
        raise AgentStopped(f"{role}: the agent ended without structured findings")
    findings = attribute([d.model_dump() for d in response.findings], role)
    note = f" (stopped early: {finish.forced_reason})" if finish.forced_reason else ""
    trace.thought(f"Returned {len(findings)} finding(s){note}.")
    if response.notes.strip():
        trace.thought(f"Notes: {response.notes.strip()[:500]}")
    return {
        "draft_findings": findings,
        "traces": [trace.as_dict()],
        "budget_limited": role in deps.budget.limited,
    }


def _stub_review(role: str, state: ReviewState, deps: EngineDeps) -> dict:
    trace = ReactTrace(agent=role)
    trace.thought(f"Offline stub: scanning the added lines for {role} patterns.")
    trace.action("get_diff", "{}")
    raw = stub.generate(role, {"diff": state["diff"]})["findings"]
    trace.observation(f"{len(raw)} pattern match(es).", tool="get_diff")
    findings = attribute(raw, role)
    trace.thought(f"Returned {len(findings)} finding(s).")
    deps.tracker.record(role, f"{deps.cfg.reviewer_model} (stub)")
    return {"draft_findings": findings, "traces": [trace.as_dict()], "budget_limited": False}
