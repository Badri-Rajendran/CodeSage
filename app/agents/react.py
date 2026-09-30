"""ReAct scaffolding.

An agent's trajectory as Reason → Act → Observe steps: its reasoning text, the
tools it called (read files, search code, run tests, ...) and what they
returned. ``TraceMiddleware`` (``app.agents.middleware``) records a live agent's
loop into this shape; the console renders it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class StepKind(StrEnum):
    THOUGHT = "thought"
    ACTION = "action"
    OBSERVATION = "observation"


@dataclass
class ReactStep:
    kind: StepKind
    content: str
    tool: str | None = None

    def as_dict(self) -> dict:
        d = {"kind": self.kind.value, "content": self.content}
        if self.tool:
            d["tool"] = self.tool
        return d


@dataclass
class ReactTrace:
    agent: str
    steps: list[ReactStep] = field(default_factory=list)

    def thought(self, content: str) -> None:
        self.steps.append(ReactStep(StepKind.THOUGHT, content))

    def action(self, tool: str, content: str) -> None:
        self.steps.append(ReactStep(StepKind.ACTION, content, tool=tool))

    def observation(self, content: str, tool: str | None = None) -> None:
        self.steps.append(ReactStep(StepKind.OBSERVATION, content, tool=tool))

    def as_dict(self) -> dict:
        return {"agent": self.agent, "steps": [s.as_dict() for s in self.steps]}
