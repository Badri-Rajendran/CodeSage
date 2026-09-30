"""LLM-as-Judge.

Scores a completed review against the diff it reviewed, on four dimensions
(correctness, groundedness, actionability, signal-to-noise) plus a holistic
score. It drives the review gate and the revision round.

Live mode: one structured call to the judge model (Opus 5 by default, a
different model from the reviewers so it isn't grading its own work), using
native structured output (``json_schema``); forced tool use, the default
``function_calling`` method, is rejected when thinking is on.
"""

from __future__ import annotations

import json
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.middleware import AgentStopped
from app.agents.schemas import JudgeResult
from app.config import Settings
from app.llm import stub
from app.llm.budget import BudgetGuard
from app.llm.client import LLMClient
from app.llm.models import build_chat_model
from app.llm.prompts import JUDGE_SYSTEM
from app.review_config import ReviewConfig


class LLMJudge:
    component = "judge"

    def __init__(
        self, client: LLMClient, cfg: ReviewConfig, budget: BudgetGuard, settings: Settings
    ):
        self.client = client
        self.cfg = cfg
        self.budget = budget
        self.settings = settings

    async def score(self, *, diff: str, findings: list[dict], summary: str = "") -> dict:
        if self.client.stubbed:
            result = stub.generate(self.component, {"diff": diff, "findings": findings})
            self.client.tracker.record(self.component, f"{self.cfg.judge_model} (stub)")
        else:
            result = await self._live(diff, findings, summary)
        result["score"] = max(0.0, min(1.0, float(result.get("score", 0.0))))
        return result

    async def _live(self, diff: str, findings: list[dict], summary: str) -> dict:
        model = build_chat_model("judge", self.cfg, self.settings).with_structured_output(
            JudgeResult, method="json_schema", include_raw=True
        )
        user = (
            f"<diff>\n{diff}\n</diff>\n\n"
            f"<review_summary>\n{summary or '(none)'}\n</review_summary>\n\n"
            f"<findings>\n{json.dumps(findings, indent=1)}\n</findings>\n\n"
            "Score this review."
        )
        out = cast(dict, await model.ainvoke([SystemMessage(JUDGE_SYSTEM), HumanMessage(user)]))
        raw = out["raw"]
        self.budget.record(self.component, self.cfg.judge_model, raw.usage_metadata)
        stop = raw.response_metadata.get("stop_reason")
        if stop == "refusal":
            raise AgentStopped("judge: the model declined to score this review")
        parsed = out.get("parsed")
        if parsed is None:
            reason = "cut off at max_tokens" if stop == "max_tokens" else out.get("parsing_error")
            raise AgentStopped(f"judge: no structured result ({reason})")
        return parsed.model_dump()
