"""ReAct scaffolding.

A reviewer follows a Reason → Act → Observe loop before synthesizing findings:
it reasons about what context it needs, *acts* by calling tools (RAG retrieval,
sandboxed tests), observes the results, then reasons over the accumulated
observations to produce findings. Every step is recorded as a `ReactStep` so the
trajectory is auditable in the review output.
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
