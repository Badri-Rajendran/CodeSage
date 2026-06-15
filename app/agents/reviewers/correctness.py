"""Correctness reviewer agent (runs sandboxed tests)."""

from __future__ import annotations

from app.agents.reviewers.base import ReviewerAgent
from app.llm.prompts import CORRECTNESS_SYSTEM


class CorrectnessReviewer(ReviewerAgent):
    name = "correctness"
    component = "correctness"
    system = CORRECTNESS_SYSTEM
    focus_query_template = (
        "function contracts, error handling, edge cases, and existing tests for "
        "the changed code"
    )
    uses_sandbox = True
