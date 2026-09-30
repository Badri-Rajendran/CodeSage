"""Background runner for asynchronous review jobs.

The REST layer inserts a 'running' review row and hands the job here; the
review runs on a detached asyncio task that publishes progress events to the
broker. A paused review is resumed the same way once a human decides. Each job
uses its own DB session (the request session is long gone by then).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.db.session import SessionFactory
from app.logging_config import get_logger
from app.realtime.broker import ReviewBroker
from app.services.review_service import EventSink, ReviewService

logger = get_logger(__name__)


class JobManager:
    def __init__(self, broker: ReviewBroker, checkpointer: Any | None = None) -> None:
        self.broker = broker
        self.checkpointer = checkpointer
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def submit(
        self,
        review_id: uuid.UUID,
        *,
        repo: str,
        pr_number: int | None,
        diff: str,
        pr_mode: bool,
    ) -> None:
        async def job(service: ReviewService, emit: EventSink) -> None:
            await service.run_review_streamed(
                review_id, repo=repo, pr_number=pr_number, diff=diff, pr_mode=pr_mode, emit=emit
            )

        self._spawn(review_id, job)

    def submit_resume(self, review_id: uuid.UUID, *, approved: bool, note: str | None) -> None:
        running = self._tasks.get(str(review_id))
        if running is not None and not running.done():  # belt and braces: decide() is atomic
            logger.warning("Review %s is already running; ignoring a second resume.", review_id)
            return
        self.broker.reopen(str(review_id))

        async def job(service: ReviewService, emit: EventSink) -> None:
            await service.resume_streamed(review_id, approved=approved, note=note, emit=emit)

        self._spawn(review_id, job)

    def _spawn(
        self,
        review_id: uuid.UUID,
        job: Callable[[ReviewService, EventSink], Awaitable[None]],
    ) -> None:
        rid = str(review_id)
        task = asyncio.create_task(self._run(review_id, job))
        self._tasks[rid] = task
        task.add_done_callback(lambda _t: self._tasks.pop(rid, None))

    async def _run(
        self,
        review_id: uuid.UUID,
        job: Callable[[ReviewService, EventSink], Awaitable[None]],
    ) -> None:
        rid = str(review_id)

        async def emit(event: dict[str, Any]) -> None:
            await self.broker.publish(rid, event)

        try:
            async with SessionFactory() as session:
                await job(ReviewService(session, self.checkpointer), emit)
        except Exception as exc:  # noqa: BLE001 — surface any job failure to the client
            logger.exception("Review job %s failed", rid)
            error = f"{type(exc).__name__}: {exc}"
            await emit({"type": "review.failed", "review_id": rid, "error": error})
            try:
                async with SessionFactory() as session:
                    await ReviewService(session).mark_failed(review_id, error)
            except Exception:  # noqa: BLE001 — best-effort status update
                logger.exception("Failed to mark review %s as failed", rid)
