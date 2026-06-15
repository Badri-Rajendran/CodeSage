"""Repository ingestion pipeline: walk a tree, chunk, embed, store."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.vector_store import VectorStore
from app.logging_config import get_logger
from app.rag.chunker import chunk_file, language_for

logger = get_logger(__name__)

_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "vendor", ".idea", ".vscode",
}
_MAX_BYTES = 512_000  # skip very large/generated files


async def ingest_path(
    session: AsyncSession,
    *,
    root: str,
    repo: str,
    replace: bool = True,
) -> dict:
    """Ingest every supported source file under `root` into pgvector."""
    store = VectorStore(session)
    if replace:
        await store.clear_repo(repo)

    root_path = Path(root)
    pending: list[dict] = []
    files = 0
    chunks_total = 0

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            fpath = Path(dirpath) / name
            rel = str(fpath.relative_to(root_path))
            if language_for(rel) is None:
                continue
            try:
                if fpath.stat().st_size > _MAX_BYTES:
                    continue
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            files += 1
            file_chunks = [c.as_dict() for c in chunk_file(repo, rel, text)]
            pending.extend(file_chunks)
            if len(pending) >= 128:
                chunks_total += await store.upsert_chunks(pending)
                pending = []

    if pending:
        chunks_total += await store.upsert_chunks(pending)

    logger.info("Ingested %s: %d files, %d chunks", repo, files, chunks_total)
    return {"repo": repo, "files": files, "chunks": chunks_total}
