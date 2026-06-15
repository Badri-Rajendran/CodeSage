"""Ingest a local repository into the pgvector RAG index.

    python -m scripts.ingest_repo --path /path/to/repo --repo owner/name
"""

from __future__ import annotations

import argparse
import asyncio

from app.db.session import SessionFactory
from app.logging_config import configure_logging, get_logger
from app.rag.pipeline import ingest_path

logger = get_logger(__name__)


async def run(path: str, repo: str, replace: bool) -> None:
    async with SessionFactory() as session:
        result = await ingest_path(session, root=path, repo=repo, replace=replace)
    logger.info("Done: %s", result)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="Local path to the repo root")
    parser.add_argument("--repo", required=True, help="Logical repo name (owner/name)")
    parser.add_argument("--no-replace", action="store_true", help="Append instead of replace")
    args = parser.parse_args()
    asyncio.run(run(args.path, args.repo, replace=not args.no_replace))


if __name__ == "__main__":
    main()
