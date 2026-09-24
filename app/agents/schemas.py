"""Structured-output schemas for the agents (used with ``ProviderStrategy``).

The model fills in ``FindingDraft``s. Code then assigns each finding an ``id``
and its ``reviewer``, so attribution can never be lost by a later model call.
Findings travel through the graph state as plain dicts (``Finding.model_dump()``).

Fields have no defaults on purpose: every field is required in the JSON schema
sent to the API, and "not applicable" is an explicit ``null``. Numeric ranges
are clamped in code rather than expressed as schema constraints.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "low", "medium", "high", "critical"]
ReviewerName = Literal["security", "correctness", "style"]

SEVERITY_RANK: dict[str, int] = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class FindingDraft(BaseModel):
    category: str = Field(description="Short kind of issue, e.g. injection, null-handling, naming")
    severity: Severity
    confidence: float = Field(description="0 to 1: how sure you are, after checking")
    path: str | None = Field(description="Repository-relative file path, or null")
    line: int | None = Field(description="Line in the PR head version (RIGHT side), or null")
    end_line: int | None = Field(description="Last line of a multi-line range, or null")
    title: str = Field(description="One line, specific")
    rationale: str = Field(description="What is wrong and why it matters")
    suggestion: str | None = Field(description="A concrete fix, or null")
    evidence: list[str] = Field(
        description="Tool results that support this, e.g. 'read_file app/x.py:40-60'"
    )


class Findings(BaseModel):
    findings: list[FindingDraft]
    notes: str = Field(description="Anything you could not anchor or verify; may be empty")


class Finding(FindingDraft):
    id: str
    reviewer: ReviewerName
    merged_from: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    finding_id: str
    action: Literal["keep", "drop", "merge"]
    merge_into: str | None = Field(description="Target finding id when action is merge")
    severity: Severity | None = Field(description="Corrected severity, or null to keep")
    title: str | None = Field(description="Reworded title, or null to keep")
    rationale: str | None = Field(description="Corrected rationale, or null to keep")
    suggestion: str | None = Field(description="Improved suggestion, or null to keep")
    line: int | None = Field(description="Corrected RIGHT-side line, or null to keep")
    end_line: int | None = Field(description="Corrected end line, or null to keep")
    reason: str = Field(description="Why you made this decision")


class ReflectionDecisions(BaseModel):
    decisions: list[Decision]
    summary: str = Field(description="One-paragraph summary of the review for the PR author")


class JudgeDimensions(BaseModel):
    correctness: float
    groundedness: float
    actionability: float
    signal_to_noise: float


class JudgeResult(BaseModel):
    score: float
    rationale: str
    dimensions: JudgeDimensions
