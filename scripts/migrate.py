"""Apply every migrations/*.sql file in order (all are idempotent).

Fresh Docker volumes run them automatically; use this for an existing database
or a non-Docker Postgres:

    python -m scripts.migrate
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.session import engine
from app.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


async def migrate() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    async with engine.begin() as conn:
        raw = await conn.get_raw_connection()
        for path in files:
            # asyncpg runs a multi-statement script in one execute() call.
            await raw.driver_connection.execute(path.read_text(encoding="utf-8"))
            logger.info("Applied %s", path.name)
    logger.info("Migrations complete (%d files).", len(files))


def main() -> None:
    configure_logging()
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
