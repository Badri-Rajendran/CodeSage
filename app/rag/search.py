"""pgvector semantic search as a workspace tool backend (local mode only).

The reviewers run concurrently, so every search opens its own short-lived
session from the factory: SQLAlchemy async sessions forbid concurrent use.
Failures are logged and return no results, so a missing index never fails a
review. SQLAlchemy is imported lazily: this module is on the engine path.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

from app.logging_config import get_logger
from app.rag.types import RetrievedChunk

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.workspace import SemanticSearch

logger = get_logger(__name__)

SessionFactoryT = Callable[[], AbstractAsyncContextManager["AsyncSession"]]


def pgvector_search(session_factory: SessionFactoryT, repo: str) -> SemanticSearch:
    async def search(query: str, top_k: int) -> list[RetrievedChunk]:
        from app.db.vector_store import VectorStore

        try:
            async with session_factory() as session:
                return await VectorStore(session).search(query, repo=repo, top_k=top_k)
        except Exception as exc:  # pragma: no cover - DB availability dependent
            logger.warning("Semantic search failed (%s); returning no results.", exc)
            return []

    return search
