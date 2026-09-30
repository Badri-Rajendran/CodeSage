"""Agent middleware for the reviewer and reflection loops.

``FinishMiddleware`` meters spend and makes the agent *finish with a structured
answer* when it runs low on budget or reaches its last allowed model call. It
does that by removing the tools and asking for the findings now: with no tools
the model has to answer, and ``ProviderStrategy`` turns the answer into the
schema. (Jumping straight to ``end`` would stop the agent with no structured
result; verified in the Phase 1 spike, see docs/decisions.md.)

``TraceMiddleware`` records what the agent actually did as a ``ReactTrace``:
reasoning text, tool calls, and tool results. The console renders that shape.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.react import ReactTrace
from app.llm.budget import BudgetGuard

FINISH_NOW = (
    "Stop investigating now: {reason}. Do not call any more tools. Return your "
    "findings in the required structured format, based on what you have verified so far."
)


class AgentStopped(RuntimeError):
    """The model refused or its output was cut off; the review can't use it."""


def _ai_messages(resp: ModelResponse | AIMessage | Any) -> list[AIMessage]:
    result = getattr(resp, "result", None)
    if result is None:
        inner = getattr(resp, "model_response", None)  # ExtendedModelResponse
        result = getattr(inner, "result", None) if inner is not None else [resp]
    return [m for m in (result or []) if isinstance(m, AIMessage)]


class FinishMiddleware(AgentMiddleware):
    def __init__(self, agent: str, model: str, guard: BudgetGuard, max_calls: int):
        super().__init__()
        self.agent_name = agent
        self.model = model
        self.guard = guard
        self.max_calls = max_calls
        self.calls = 0
        self.forced_reason: str | None = None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        reason = None
        if self.calls >= 1 and self.guard.should_finish(self.agent_name):
            reason = "the review budget for this agent is nearly used"
            self.guard.mark_limited(self.agent_name)
        elif self.calls >= self.max_calls - 1:
            reason = "this is your last allowed step"
        if reason and request.tools:
            self.forced_reason = reason
            request = request.override(
                tools=[],
                messages=[*request.messages, HumanMessage(FINISH_NOW.format(reason=reason))],
            )
        self.calls += 1
        try:
            resp = await handler(request)
        except StructuredOutputValidationError as exc:
            # The final answer didn't parse: bill it, then explain why if we can.
            self._check(exc.ai_message)
            raise
        for msg in _ai_messages(resp):
            self._check(msg)
        return resp

    def _check(self, msg: AIMessage) -> None:
        self.guard.record(self.agent_name, self.model, msg.usage_metadata)
        stop = msg.response_metadata.get("stop_reason")
        if stop == "refusal":
            raise AgentStopped(f"{self.agent_name}: the model declined to review this change")
        if stop == "max_tokens" and not msg.tool_calls:
            raise AgentStopped(f"{self.agent_name}: the answer was cut off at max_tokens")


def _short_args(args: dict[str, Any], limit: int = 200) -> str:
    text = json.dumps(args, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _text_of(msg: AIMessage) -> str:
    if isinstance(msg.content, str):
        return msg.content
    return "\n".join(
        b.get("text", "") for b in msg.content if isinstance(b, dict) and b.get("type") == "text"
    )


class TraceMiddleware(AgentMiddleware):
    def __init__(self, trace: ReactTrace):
        super().__init__()
        self.trace = trace

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        resp = await handler(request)
        for msg in _ai_messages(resp):
            if not msg.tool_calls:
                continue  # the final structured answer is summarised by the node
            text = _text_of(msg).strip()
            if text:
                self.trace.thought(text[:1000])
            for call in msg.tool_calls:
                self.trace.action(call["name"], _short_args(call.get("args") or {}))
        return resp

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        result = await handler(request)
        if isinstance(result, ToolMessage):
            content = result.content if isinstance(result.content, str) else str(result.content)
            self.trace.observation(content[:500], tool=request.tool_call.get("name"))
        return result
