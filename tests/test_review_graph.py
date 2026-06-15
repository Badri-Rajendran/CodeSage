"""End-to-end test of the LangGraph review pipeline in stub mode (no API key, no DB)."""

from __future__ import annotations

import pytest

from app.agents.graph import build_review_graph, make_deps
from app.agents.tools import ReviewTools
from app.config import get_settings
from app.llm.client import LLMClient
from app.llm.telemetry import CostTracker

VULN_DIFF = (
    "diff --git a/run.py b/run.py\n"
    "--- a/run.py\n"
    "+++ b/run.py\n"
    "@@ -1,2 +1,4 @@\n"
    " import os\n"
    "+def handle(cmd):\n"
    "+    os.system(cmd)\n"
)

CLEAN_DIFF = (
    "diff --git a/util.py b/util.py\n"
    "--- a/util.py\n"
    "+++ b/util.py\n"
    "@@ -1,2 +1,4 @@\n"
    " def add(a, b):\n"
    "     return a + b\n"
    "+def sub(a, b):\n"
    "+    return a - b\n"
)


async def _run(diff: str) -> dict:
    settings = get_settings()
    client = LLMClient(settings, CostTracker())
    assert client.stubbed, "test must run in stub mode"
    tools = ReviewTools("demo/x", diff, settings=settings)
    graph = build_review_graph(make_deps(client, tools, settings))
    return await graph.ainvoke({"repo": "demo/x", "pr_number": 1, "diff": diff})


@pytest.mark.asyncio
async def test_vulnerable_diff_flags_critical_and_requires_approval():
    state = await _run(VULN_DIFF)
    findings = state["findings"]
    assert findings, "expected at least one finding"
    assert any(f["severity"] == "critical" for f in findings)
    assert any(f.get("reviewer") == "security" for f in findings)
    assert state["judge_score"] is not None
    # Critical finding trips the human-in-the-loop gate.
    assert state["requires_human_approval"] is True
    assert state["summary"]


@pytest.mark.asyncio
async def test_clean_diff_passes_without_approval():
    state = await _run(CLEAN_DIFF)
    assert state["findings"] == []
    assert state["judge_score"] >= get_settings().hitl_threshold
    assert state["requires_human_approval"] is False


@pytest.mark.asyncio
async def test_traces_are_recorded_for_each_reviewer():
    state = await _run(VULN_DIFF)
    agents = {t["agent"] for t in state["traces"]}
    assert agents == {"security", "correctness", "style"}
    # Each trace has the ReAct shape: at least one thought and one action.
    for trace in state["traces"]:
        kinds = {s["kind"] for s in trace["steps"]}
        assert "thought" in kinds
        assert "action" in kinds
