"""Per-review spend control.

The total (default $0.50) is split into an allowance per reviewer (20% each)
and a reserve (the remaining 40%) that only reflection, the judge and a
revision round may use, so the reviewers can never starve the later stages.

Enforcement happens *between* model calls: an agent over its allowance is told
to finish (see ``app.agents.middleware.FinishMiddleware``), and a stage whose
minimum cost no longer fits in what's left is skipped. A single in-flight call
can overshoot slightly; that is bounded by the per-call ``max_tokens``, and the
real total is always reported.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.llm.telemetry import CostTracker

REVIEWERS = ("security", "correctness", "style")
REVIEWER_SHARE = 0.20
# An agent is asked to wrap up when it has this fraction of its allowance left,
# leaving room for the final (tool-less) structured answer.
FINALIZE_FRACTION = 0.30
# Minimum budget a reserve stage needs before it is allowed to start (share of total).
MIN_START_SHARE = {"reflection": 0.04, "judge": 0.06, "revise": 0.16}


@dataclass
class CallUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cache_write_1h_tokens: int


def usage_from_metadata(usage: Mapping[str, Any] | None) -> CallUsage:
    """Split LangChain ``usage_metadata`` into Anthropic-style billing buckets.

    LangChain's ``input_tokens`` *includes* cached tokens. Cache writes are
    reported either as ``cache_creation`` or, when TTL detail is present, as
    ``ephemeral_5m_input_tokens`` / ``ephemeral_1h_input_tokens`` (with
    ``cache_creation`` then set to 0).
    """
    usage = usage or {}
    details: Mapping[str, Any] = usage.get("input_token_details") or {}
    read = details.get("cache_read") or 0
    w5 = details.get("ephemeral_5m_input_tokens") or 0
    w1h = details.get("ephemeral_1h_input_tokens") or 0
    if not (w5 or w1h):
        w5 = details.get("cache_creation") or 0
    fresh = max(0, (usage.get("input_tokens") or 0) - read - w5 - w1h)
    return CallUsage(fresh, usage.get("output_tokens") or 0, read, w5, w1h)


@dataclass
class BudgetGuard:
    total_usd: float
    tracker: CostTracker = field(default_factory=CostTracker)
    spent_by: dict[str, float] = field(default_factory=dict)
    limited: set[str] = field(default_factory=set)

    @property
    def total_spent(self) -> float:
        return sum(self.spent_by.values())

    @property
    def remaining(self) -> float:
        return max(0.0, self.total_usd - self.total_spent)

    def allowance(self, agent: str) -> float:
        """Reviewers get a fixed share; reserve stages may use whatever is left."""
        if agent in REVIEWERS:
            return self.total_usd * REVIEWER_SHARE
        return self.spent_by.get(agent, 0.0) + self.remaining

    def should_finish(self, agent: str) -> bool:
        allowance = self.allowance(agent)
        return self.spent_by.get(agent, 0.0) >= allowance * (1 - FINALIZE_FRACTION)

    def can_start(self, stage: str) -> bool:
        return self.remaining >= self.total_usd * MIN_START_SHARE.get(stage, 0.0)

    def record(
        self, agent: str, model: str, usage_metadata: Mapping[str, Any] | None
    ) -> float:
        u = usage_from_metadata(usage_metadata)
        ev = self.tracker.record(
            agent,
            model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_write_tokens=u.cache_write_tokens,
            cache_write_1h_tokens=u.cache_write_1h_tokens,
        )
        self.spent_by[agent] = self.spent_by.get(agent, 0.0) + ev.cost
        return ev.cost

    def mark_limited(self, agent: str) -> None:
        self.limited.add(agent)
