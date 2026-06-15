"""Async Claude client wrapper.

A single choke point for every model call so telemetry, retries, structured
outputs, and the offline stub are handled consistently. Uses the official
Anthropic SDK with adaptive thinking + the effort parameter, and falls back to
the deterministic stub when no API key is configured.
"""

from __future__ import annotations

import json
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings
from app.llm import stub
from app.llm.telemetry import CostTracker
from app.logging_config import get_logger

logger = get_logger(__name__)

try:  # The SDK is a hard dependency at runtime, but keep import errors legible.
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]


class LLMClient:
    """Wraps Claude structured-output calls and meters their cost."""

    def __init__(self, settings: Settings | None = None, tracker: CostTracker | None = None):
        self.settings = settings or get_settings()
        self.tracker = tracker or CostTracker()
        self._stub = not self.settings.has_llm or anthropic is None
        self._client = (
            anthropic.AsyncAnthropic(api_key=self.settings.anthropic_api_key)
            if not self._stub
            else None
        )
        if self._stub:
            logger.warning(
                "LLMClient running in STUB mode (no ANTHROPIC_API_KEY). "
                "Reviews use deterministic heuristics, not the model."
            )

    @property
    def stubbed(self) -> bool:
        return self._stub

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _call_model(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        model: str,
        max_tokens: int,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Single structured Claude call. Returns (parsed_json, usage_counts)."""
        assert self._client is not None
        # Stream for headroom on larger outputs; structured outputs + adaptive
        # thinking are compatible.
        async with self._client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.settings.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            message = await stream.get_final_message()

        if message.stop_reason == "refusal":
            raise RuntimeError(
                f"Model refused the request: {getattr(message, 'stop_details', None)}"
            )

        text = next((b.text for b in message.content if b.type == "text"), "")
        parsed = json.loads(text) if text else {}
        usage = {
            "input_tokens": getattr(message.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(message.usage, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(message.usage, "cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
        }
        return parsed, usage

    async def structured(
        self,
        component: str,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        stub_payload: dict[str, Any],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Run a structured-output call (or the stub) and record telemetry."""
        model = model or self.settings.model
        max_tokens = max_tokens or self.settings.max_tokens

        if self._stub:
            result = stub.generate(component, stub_payload)
            # Record a nominal, zero-cost telemetry event so the pipeline shape is
            # identical online and offline.
            self.tracker.record(component, f"{model} (stub)", input_tokens=0, output_tokens=0)
            return result

        parsed, usage = await self._call_model(
            system=system,
            user=user,
            schema=schema,
            model=model,
            max_tokens=max_tokens,
        )
        self.tracker.record(component, model, **usage)
        return parsed
