"""Chat-model factory for the agents (LangChain ``ChatAnthropic``).

Always returns a model *instance*: LangChain's automatic structured-output
detection doesn't recognise the Claude 5 model names, so agents pass this
instance together with an explicit ``ProviderStrategy``.
"""

from __future__ import annotations

from typing import Literal

from langchain_anthropic import ChatAnthropic

from app.config import Settings, get_settings
from app.review_config import ReviewConfig

Role = Literal["reviewer", "reflection", "judge"]

# Output cap per call. It bounds how far a single in-flight call can overshoot
# the budget, and it includes adaptive-thinking tokens.
MAX_TOKENS: dict[Role, int] = {"reviewer": 4000, "reflection": 4000, "judge": 3000}


def model_name(role: Role, cfg: ReviewConfig) -> str:
    return cfg.judge_model if role == "judge" else cfg.reviewer_model


def build_chat_model(
    role: Role, cfg: ReviewConfig, settings: Settings | None = None
) -> ChatAnthropic:
    settings = settings or get_settings()
    return ChatAnthropic(  # type: ignore[call-arg]
        model=model_name(role, cfg),
        api_key=settings.anthropic_api_key,
        max_tokens=MAX_TOKENS[role],
        thinking={"type": "adaptive"},
        reasoning_effort=cfg.judge_effort if role == "judge" else cfg.reviewer_effort,
        # Top-level automatic prompt caching: each agent's system prompt, tools
        # and diff prefix are re-read from cache on every turn of its loop.
        model_kwargs={"cache_control": {"type": "ephemeral"}},
    )
