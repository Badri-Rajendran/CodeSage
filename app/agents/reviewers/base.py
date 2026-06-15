"""Base reviewer agent implementing the ReAct loop."""

from __future__ import annotations

from app.agents.react import ReactTrace
from app.agents.tools import ReviewTools
from app.db.vector_store import RetrievedChunk
from app.llm.client import LLMClient
from app.llm.prompts import FINDINGS_SCHEMA


def _format_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(no codebase context retrieved)"
    parts = []
    for c in chunks:
        parts.append(f"### {c.cite()} (similarity {c.score:.2f})\n```\n{c.content}\n```")
    return "\n\n".join(parts)


class ReviewerAgent:
    """A single-focus reviewer. Subclasses set name, system prompt, and focus query."""

    name: str = "reviewer"
    component: str = "reviewer"
    system: str = ""
    focus_query_template: str = "code relevant to this change"
    uses_sandbox: bool = False

    def __init__(self, client: LLMClient):
        self.client = client

    def _focus_query(self, repo: str) -> str:
        return self.focus_query_template

    async def review(
        self, *, repo: str, pr_number: int | None, diff: str, tools: ReviewTools
    ) -> dict:
        trace = ReactTrace(agent=self.name)

        # ── Reason: decide what context to gather ──────────────────────────────
        trace.thought(
            f"As the {self.name} reviewer, I need surrounding code to ground my "
            f"findings. I'll retrieve context relevant to: {self._focus_query(repo)}"
        )

        # ── Act: retrieve RAG context ─────────────────────────────────────────
        query = f"{self._focus_query(repo)}\n\nDiff under review:\n{diff[:2000]}"
        chunks = await tools.retrieve_context(query)
        trace.action("retrieve_context", self._focus_query(repo))
        trace.observation(
            f"Retrieved {len(chunks)} context snippet(s): "
            + ", ".join(c.cite() for c in chunks)
            if chunks
            else "No matching context found in the indexed codebase.",
            tool="retrieve_context",
        )

        # ── Act: optionally run sandboxed tests ───────────────────────────────
        sandbox_summary = ""
        if self.uses_sandbox:
            result = await tools.run_sandboxed_tests()
            trace.action("run_sandboxed_tests", "execute diff-added tests")
            trace.observation(result.summary, tool="run_sandboxed_tests")
            if result.ran:
                sandbox_summary = (
                    f"\n\nSandboxed test run: {result.summary}\n"
                    f"return code: {result.returncode}\n"
                    f"stdout tail:\n{result.stdout[-1000:]}\n"
                    f"stderr tail:\n{result.stderr[-1000:]}"
                )

        # ── Reason: synthesize findings from diff + observations ──────────────
        trace.thought("Synthesizing findings from the diff and gathered observations.")
        user = (
            f"Repository: {repo}\nPR: {pr_number}\n\n"
            f"## Diff\n```diff\n{diff}\n```\n\n"
            f"## Retrieved codebase context\n{_format_context(chunks)}"
            f"{sandbox_summary}"
        )
        result = await self.client.structured(
            self.component,
            system=self.system,
            user=user,
            schema=FINDINGS_SCHEMA,
            stub_payload={"diff": diff},
        )
        findings = result.get("findings", [])
        for f in findings:
            f["reviewer"] = self.name
        trace.thought(f"Produced {len(findings)} finding(s).")

        return {"findings": findings, "trace": trace.as_dict()}
