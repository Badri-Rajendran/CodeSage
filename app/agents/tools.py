"""Tools available to the reviewer agents.

Two tools back the ReAct loop:
  - retrieve_context: pgvector RAG over the ingested codebase
  - run_sandboxed_tests: execute diff-added tests in the sandbox

Each tool exposes a JSON schema (for the MCP server and for model-driven tool use)
and an async implementation.

Concurrency note: the three reviewers run in parallel, so RAG retrieval must NOT
share one AsyncSession (SQLAlchemy async sessions forbid concurrent use). Each
``retrieve_context`` call therefore opens its own short-lived session from the
factory; the request-scoped session is reserved for the sequential write path.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

from app.agents.sandbox import Sandbox, SandboxResult
from app.config import Settings, get_settings
from app.logging_config import get_logger
from app.rag.types import RetrievedChunk

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SessionFactoryT = Callable[[], AbstractAsyncContextManager["AsyncSession"]]

TOOL_SCHEMAS = [
    {
        "name": "retrieve_context",
        "description": (
            "Retrieve the most relevant snippets from the ingested codebase for a "
            "query, so review findings can be grounded in real surrounding code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "top_k": {"type": "integer", "description": "How many snippets"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_sandboxed_tests",
        "description": (
            "Execute the test files added by the pull request in an isolated "
            "sandbox and report pass/fail output."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


class ReviewTools:
    def __init__(
        self,
        repo: str,
        diff: str,
        *,
        settings: Settings | None = None,
        session_factory: SessionFactoryT | None = None,
    ):
        self.settings = settings or get_settings()
        self.repo = repo
        self.diff = diff
        self.session_factory = session_factory
        self.sandbox = Sandbox(self.settings)

    @property
    def rag_enabled(self) -> bool:
        return self.session_factory is not None

    async def retrieve_context(
        self, query: str, top_k: int | None = None
    ) -> list[RetrievedChunk]:
        if self.session_factory is None:
            return []
        from app.db.vector_store import VectorStore  # server extra; only with a DB

        try:
            async with self.session_factory() as session:
                return await VectorStore(session).search(
                    query, repo=self.repo, top_k=top_k or self.settings.rag_top_k
                )
        except Exception as exc:  # pragma: no cover - DB availability dependent
            logger.warning("RAG retrieval failed (%s); proceeding without context.", exc)
            return []

    async def run_sandboxed_tests(self) -> SandboxResult:
        return await self.sandbox.run_tests(self.diff)
