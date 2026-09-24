"""Stub switch and per-review cost tracker.

With no ``ANTHROPIC_API_KEY`` every agent, reflection and judge call is routed
to the deterministic heuristics in ``app.llm.stub``, so the pipeline (and the
test suite) runs offline with the same shape as live mode. Live calls go
through LangChain ``ChatAnthropic`` (``app.llm.models``), not through this
class; they are metered into the same ``CostTracker`` by ``BudgetGuard``.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.llm.telemetry import CostTracker
from app.logging_config import get_logger

logger = get_logger(__name__)


class LLMClient:
    def __init__(self, settings: Settings | None = None, tracker: CostTracker | None = None):
        self.settings = settings or get_settings()
        self.tracker = tracker or CostTracker()
        self._stub = not self.settings.has_llm
        if self._stub:
            logger.warning(
                "LLMClient running in STUB mode (no ANTHROPIC_API_KEY). "
                "Reviews use deterministic heuristics, not the model."
            )

    @property
    def stubbed(self) -> bool:
        return self._stub
