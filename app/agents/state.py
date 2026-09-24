"""LangGraph state for the review pipeline.

The three reviewer nodes run in parallel. Keys they all write need reducers so
concurrent writes merge: ``draft_findings`` and ``traces`` concatenate, and
``budget_limited`` ORs. Every other key is written by one node at a time.

``ReviewService._merge_delta`` mirrors these reducers for streamed updates;
keep the two in step.

Findings are plain dicts (``app.agents.schemas.Finding.model_dump()``).
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

# Keys with non-overwrite reducers, and how to merge them (used by ReviewService).
ADDITIVE_KEYS = ("draft_findings", "traces")
OR_KEYS = ("budget_limited",)


class ReviewState(TypedDict, total=False):
    # Inputs
    repo: str
    pr_number: int | None
    diff: str
    pr: dict | None  # PR context: title, body, base_sha, head_sha, url

    # Parallel reviewer outputs (merged via reducers)
    draft_findings: Annotated[list[dict], operator.add]
    traces: Annotated[list[dict], operator.add]
    budget_limited: Annotated[bool, operator.or_]

    # Reflection (and revise)
    findings: list[dict]
    summary: str
    reflection_trace: dict | None
    revision_count: int

    # Judge
    judge_score: float | None
    judge_rationale: str
    judge_dimensions: dict

    # Gate
    gate_tripped: bool
    gate_reasons: list[str]
    requires_human_approval: bool  # = gate_tripped; kept for API compatibility
    decision: str | None  # "approved" | "rejected" (local mode, set on resume)
    decision_note: str | None

    # Publish
    github_review_url: str | None
