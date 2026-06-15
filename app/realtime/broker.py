"""In-memory pub/sub broker that fans review progress events out to SSE clients.

A single review job publishes a small stream of events (``review.started``,
one ``stage.completed`` per graph node, ``telemetry.update``, and a terminal
``review.completed`` / ``review.failed``). Any number of HTTP clients can
subscribe to a review's stream; late subscribers are replayed the buffered
events so a browser that connects (or reconnects) mid-flight still renders the
full timeline.

This is deliberately process-local: CodeSage runs as a single Uvicorn worker, so
an asyncio-based broker is sufficient and dependency-free. For a multi-worker or
multi-replica deployment, swap the buffer/fan-out for Redis pub/sub (the public
``publish`` / ``subscribe`` surface stays the same).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

TERMINAL_EVENTS = {"review.completed", "review.failed"}

# Cap the replay buffer per review so a long-lived process doesn't grow unbounded.
_MAX_BUFFERED_EVENTS = 200


class ReviewBroker:
    """Fan review-progress events out to subscribed SSE generators."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._buffer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._done: set[str] = set()

    async def publish(self, review_id: str, event: dict[str, Any]) -> None:
        """Record an event and push it to every live subscriber of ``review_id``."""
        buf = self._buffer[review_id]
        buf.append(event)
        if len(buf) > _MAX_BUFFERED_EVENTS:
            del buf[: len(buf) - _MAX_BUFFERED_EVENTS]

        for queue in list(self._subscribers.get(review_id, ())):
            queue.put_nowait(event)

        if event.get("type") in TERMINAL_EVENTS:
            self._done.add(review_id)

    async def subscribe(self, review_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Return a queue pre-loaded with buffered events, then live ones."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for event in self._buffer.get(review_id, []):
            queue.put_nowait(event)
        self._subscribers[review_id].add(queue)
        return queue

    def unsubscribe(self, review_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subscribers.get(review_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(review_id, None)

    def is_done(self, review_id: str) -> bool:
        return review_id in self._done
