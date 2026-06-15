"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ReviewRequest(BaseModel):
    repo: str = Field(..., description="owner/name", examples=["octocat/hello-world"])
    pr_number: int | None = Field(None, description="PR number (required if no diff given)")
    diff: str | None = Field(None, description="Unified diff. If omitted, fetched from GitHub.")

    @model_validator(mode="after")
    def _need_diff_or_pr(self) -> ReviewRequest:
        if not self.diff and self.pr_number is None:
            raise ValueError("Provide either `diff` or `pr_number`.")
        return self


class Finding(BaseModel):
    title: str
    severity: str
    rationale: str
    confidence: float
    file: str | None = None
    line: int | None = None
    suggestion: str | None = None
    reviewer: str | None = None


class ReviewResponse(BaseModel):
    id: str
    repo: str
    pr_number: int | None
    findings: list[Finding]
    summary: str | None
    judge_score: float | None
    judge_rationale: str | None = None
    judge_dimensions: dict | None = None
    requires_human_approval: bool
    approved: bool | None = None
    traces: list[dict] | None = None
    telemetry: dict | None = None


class ApprovalRequest(BaseModel):
    approved: bool = Field(..., description="Approve (true) or reject (false) the review.")


class IngestRequest(BaseModel):
    repo: str = Field(..., description="Logical repo name to tag the chunks with")
    path: str = Field(..., description="Local filesystem path to the repo root")
    replace: bool = Field(True, description="Clear existing chunks for this repo first")


class IngestResponse(BaseModel):
    repo: str
    files: int
    chunks: int
