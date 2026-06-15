"""LLM-as-Judge.

Scores the quality of a completed review against the diff it reviewed, across
four dimensions (correctness, groundedness, actionability, signal-to-noise) plus
a holistic score. Used both inside the live graph (to drive the human-in-the-loop
gate) and by the offline eval harness.
"""

from __future__ import annotations

import json

from app.llm.client import LLMClient
from app.llm.prompts import JUDGE_SCHEMA, JUDGE_SYSTEM


class LLMJudge:
    component = "judge"

    def __init__(self, client: LLMClient):
        self.client = client
        self.settings = client.settings

    async def score(self, *, diff: str, findings: list[dict], summary: str = "") -> dict:
        user = (
            "## Diff that was reviewed\n"
            f"```diff\n{diff}\n```\n\n"
            "## Review summary\n"
            f"{summary or '(none)'}\n\n"
            "## Findings produced by the review\n"
            f"```json\n{json.dumps(findings, indent=2)}\n```\n\n"
            "Score this review."
        )
        result = await self.client.structured(
            self.component,
            system=JUDGE_SYSTEM,
            user=user,
            schema=JUDGE_SCHEMA,
            stub_payload={"diff": diff, "findings": findings},
            model=self.settings.judge_model,
        )
        # Clamp defensively.
        score = float(result.get("score", 0.0))
        result["score"] = max(0.0, min(1.0, score))
        return result
