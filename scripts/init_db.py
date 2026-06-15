"""Create the pgvector extension and all tables.

Idempotent. For Docker the migration SQL runs on first boot; this script covers
local/non-Docker setups and re-runs.

    python -m scripts.init_db
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.models import Base
from app.db.session import engine
from app.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


async def init() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized (pgvector + tables).")


def main() -> None:
    configure_logging()
    asyncio.run(init())


if __name__ == "__main__":
    main()
