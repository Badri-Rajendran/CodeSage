"""Self-reflection agent.

Takes the union of draft findings from the parallel reviewers and critiques them:
de-duplicates, removes unsupported claims, calibrates severity, and writes a
concise review summary. This is the "reflect" half of ReAct + self-reflection.
"""

from __future__ import annotations

import json

from app.llm.client import LLMClient
from app.llm.prompts import REFLECTION_SCHEMA, REFLECTION_SYSTEM


class ReflectionAgent:
    component = "reflection"

    def __init__(self, client: LLMClient):
        self.client = client

    async def reflect(self, *, diff: str, draft_findings: list[dict]) -> dict:
        user = (
            "## Diff under review\n"
            f"```diff\n{diff}\n```\n\n"
            "## Draft findings from the parallel reviewers\n"
            f"```json\n{json.dumps(draft_findings, indent=2)}\n```\n\n"
            "Consolidate and correct these into a final, high-signal set, and write "
            "a one-paragraph summary."
        )
        result = await self.client.structured(
            self.component,
            system=REFLECTION_SYSTEM,
            user=user,
            schema=REFLECTION_SCHEMA,
            stub_payload={"diff": diff, "findings": draft_findings},
        )
        return {
            "findings": result.get("findings", draft_findings),
            "summary": result.get("summary", ""),
        }
