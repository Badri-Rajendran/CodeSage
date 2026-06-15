"""Background runner for asynchronous review jobs.

The REST layer inserts a 'running' review row and hands the job here; we drive
the streaming review on a detached asyncio task, publishing progress events to
the broker as the graph advances. Each job uses its own DB session (the request
session is long gone by the time the job runs).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.db.session import SessionFactory
from app.logging_config import get_logger
from app.realtime.broker import ReviewBroker
from app.services.review_service import ReviewService

logger = get_logger(__name__)


class JobManager:
    def __init__(self, broker: ReviewBroker) -> None:
        self.broker = broker
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def submit(
        self, review_id: uuid.UUID, *, repo: str, pr_number: int | None, diff: str
    ) -> None:
        task = asyncio.create_task(self._run(review_id, repo, pr_number, diff))
        self._tasks[str(review_id)] = task
        task.add_done_callback(lambda _t: self._tasks.pop(str(review_id), None))

    async def _run(
        self, review_id: uuid.UUID, repo: str, pr_number: int | None, diff: str
    ) -> None:
        rid = str(review_id)

        async def publish(event: dict[str, Any]) -> None:
            await self.broker.publish(rid, event)

        try:
            async with SessionFactory() as session:
                service = ReviewService(session)
                await service.run_review_streamed(
                    review_id, repo=repo, pr_number=pr_number, diff=diff, publish=publish
                )
        except Exception as exc:  # noqa: BLE001 — surface any job failure to the client
            logger.exception("Review job %s failed", rid)
            await publish({"type": "review.failed", "review_id": rid, "error": str(exc)})
            try:
                async with SessionFactory() as session:
                    await ReviewService(session).mark_failed(review_id, str(exc))
            except Exception:  # noqa: BLE001 — best-effort status update
                logger.exception("Failed to mark review %s as failed", rid)
