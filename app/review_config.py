"""Per-repository review configuration (``.codesage.yml``).

Every key is optional. Unset keys fall back to the environment settings
(``CODESAGE_*``), which in turn have the defaults from the design: Sonnet 5
reviewers, an Opus 5 judge, a $0.50 budget, and a 0.6 gate threshold.

Unknown keys and bad values raise ``ReviewConfigError`` with a message that
names the offending key, so a typo fails the run loudly instead of silently
reviewing with defaults.

    models:   {reviewer: claude-sonnet-5, judge: claude-opus-5}
    effort:   {reviewer: medium, judge: high}
    budget_usd: 0.50
    gate:     {threshold: 0.6, fail_on: [critical, high]}
    ignore_paths: ["docs/**"]        # added to the built-in ignores
    max_files: 50
    tests:    {setup: "pip install -e .[dev]", command: "pytest -q {target}", timeout_s: 300}
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings, get_settings
from app.workspace import TestSpec

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Severity = Literal["info", "low", "medium", "high", "critical"]

DEFAULT_IGNORES: tuple[str, ...] = (
    "**/*.lock",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.map",
    "**/node_modules/**",
    "**/vendor/**",
    "dist/**",
    "build/**",
)


class ReviewConfigError(ValueError):
    """``.codesage.yml`` is invalid; the message names the offending key."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Models(_Strict):
    reviewer: str | None = None
    judge: str | None = None


class _Effort(_Strict):
    reviewer: Effort | None = None
    judge: Effort | None = None


class _Gate(_Strict):
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    fail_on: list[Severity] = Field(default_factory=lambda: ["critical", "high"])


class _Tests(_Strict):
    setup: str | None = None
    command: str | None = None
    timeout_s: int = Field(default=300, ge=10, le=3600)


class ReviewConfigFile(_Strict):
    """The raw ``.codesage.yml`` schema."""

    models: _Models = Field(default_factory=_Models)
    effort: _Effort = Field(default_factory=_Effort)
    budget_usd: float | None = Field(default=None, gt=0.0, le=10.0)
    gate: _Gate = Field(default_factory=_Gate)
    ignore_paths: list[str] = Field(default_factory=list)
    max_files: int = Field(default=50, ge=1, le=500)
    tests: _Tests = Field(default_factory=_Tests)


@dataclass(frozen=True)
class ReviewConfig:
    """Effective configuration for one review: file values over env settings."""

    reviewer_model: str
    judge_model: str
    reviewer_effort: str
    judge_effort: str
    budget_usd: float
    gate_threshold: float
    fail_on: tuple[str, ...]
    ignore_paths: tuple[str, ...]
    max_files: int
    tests: TestSpec | None

    @classmethod
    def from_file(cls, raw: ReviewConfigFile, settings: Settings) -> ReviewConfig:
        tests = (
            TestSpec(command=raw.tests.command, setup=raw.tests.setup,
                     timeout_s=raw.tests.timeout_s)
            if raw.tests.command
            else None
        )
        return cls(
            reviewer_model=raw.models.reviewer or settings.model,
            judge_model=raw.models.judge or settings.judge_model,
            reviewer_effort=raw.effort.reviewer or settings.reviewer_effort,
            judge_effort=raw.effort.judge or settings.judge_effort,
            budget_usd=raw.budget_usd if raw.budget_usd is not None else settings.budget_usd,
            gate_threshold=(
                raw.gate.threshold if raw.gate.threshold is not None else settings.hitl_threshold
            ),
            fail_on=tuple(raw.gate.fail_on),
            ignore_paths=DEFAULT_IGNORES + tuple(raw.ignore_paths),
            max_files=raw.max_files,
            tests=tests,
        )


def default_review_config(settings: Settings | None = None) -> ReviewConfig:
    """The configuration when a repo has no ``.codesage.yml`` (and in local mode)."""
    return ReviewConfig.from_file(ReviewConfigFile(), settings or get_settings())


def load_review_config(path: str | Path | None, settings: Settings | None = None) -> ReviewConfig:
    """Load ``path`` if it exists; a missing file means all defaults."""
    settings = settings or get_settings()
    if path is None or not Path(path).is_file():
        return default_review_config(settings)
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ReviewConfigError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewConfigError(f"{path}: top level must be a mapping")
    try:
        raw = ReviewConfigFile.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ReviewConfigError(f"{path}: {problems}") from exc
    return ReviewConfig.from_file(raw, settings)
