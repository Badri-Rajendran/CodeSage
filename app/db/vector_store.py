"""pgvector-backed retrieval over ingested code chunks."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CodeChunk
from app.rag.embeddings import Embedder
from app.rag.types import RetrievedChunk

__all__ = ["RetrievedChunk", "VectorStore"]


class VectorStore:
    def __init__(self, session: AsyncSession, embedder: Embedder | None = None):
        self.session = session
        self.embedder = embedder or Embedder()

    async def upsert_chunks(self, chunks: list[dict]) -> int:
        """Insert chunks with freshly computed embeddings. Returns count."""
        if not chunks:
            return 0
        vectors = await self.embedder.embed([c["content"] for c in chunks])
        rows = [
            CodeChunk(
                repo=c["repo"],
                path=c["path"],
                start_line=c["start_line"],
                end_line=c["end_line"],
                language=c.get("language"),
                content=c["content"],
                embedding=vec,
            )
            for c, vec in zip(chunks, vectors, strict=True)
        ]
        self.session.add_all(rows)
        await self.session.commit()
        return len(rows)

    async def clear_repo(self, repo: str) -> None:
        await self.session.execute(delete(CodeChunk).where(CodeChunk.repo == repo))
        await self.session.commit()

    async def search(
        self, query: str, *, repo: str | None = None, top_k: int = 6
    ) -> list[RetrievedChunk]:
        """Cosine-similarity search for the chunks most relevant to `query`."""
        embedding = (await self.embedder.embed([query]))[0]
        distance = CodeChunk.embedding.cosine_distance(embedding)
        stmt = select(CodeChunk, distance.label("distance"))
        if repo:
            stmt = stmt.where(CodeChunk.repo == repo)
        stmt = stmt.order_by(distance).limit(top_k)

        result = await self.session.execute(stmt)
        out: list[RetrievedChunk] = []
        for chunk, dist in result.all():
            out.append(
                RetrievedChunk(
                    repo=chunk.repo,
                    path=chunk.path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    score=1.0 - float(dist),  # cosine similarity
                )
            )
        return out
