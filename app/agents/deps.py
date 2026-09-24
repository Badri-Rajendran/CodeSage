"""Everything a review graph needs, bound once per review."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from app.config import Settings
from app.llm.budget import BudgetGuard
from app.llm.client import LLMClient
from app.review_config import ReviewConfig
from app.workspace import Workspace

GraphMode = Literal["report", "action", "local"]

# Called by the `publish` node with the final state; returns a state update
# (e.g. {"github_review_url": ...}). None means nothing is published.
Publisher = Callable[[dict], Awaitable[dict]]


@dataclass
class EngineDeps:
    # `client` is the stub switch and holds the review's CostTracker; the live
    # agents call Claude through LangChain (app.llm.models), not through it.
    client: LLMClient
    workspace: Workspace
    settings: Settings
    cfg: ReviewConfig
    budget: BudgetGuard
    publisher: Publisher | None = None

    @property
    def stubbed(self) -> bool:
        return self.client.stubbed

    @property
    def tracker(self):
        return self.client.tracker
