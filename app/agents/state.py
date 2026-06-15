"""LangGraph state for the review pipeline.

The three reviewer nodes run in parallel and each appends to `draft_findings` and
`traces`; those keys use additive reducers so concurrent writes merge instead of
clobbering one another.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class ReviewState(TypedDict, total=False):
    # Inputs
    repo: str
    pr_number: int | None
    diff: str

    # Parallel reviewer outputs (merged via additive reducers)
    draft_findings: Annotated[list[dict], operator.add]
    traces: Annotated[list[dict], operator.add]

    # Post-reflection
    findings: list[dict]
    summary: str

    # Judge
    judge_score: float
    judge_rationale: str
    judge_dimensions: dict

    # Human-in-the-loop gate
    requires_human_approval: bool
    approved: bool | None
