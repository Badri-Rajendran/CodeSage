"""REST API routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ApprovalRequest,
    EvalCompareRequest,
    EvalRunRequest,
    IngestRequest,
    IngestResponse,
    ReviewJob,
    ReviewRequest,
    ReviewResponse,
)
from app.config import get_settings
from app.db.session import get_session
from app.eval import store as eval_store
from app.github.client import GitHubClient
from app.rag.pipeline import ingest_path
from app.services.review_service import ReviewService

router = APIRouter(prefix="/api/v1", tags=["codesage"])


async def _resolve_diff(body: ReviewRequest) -> str:
    """Return the diff from the request, or fetch it from GitHub by PR number."""
    diff = body.diff
    if not diff:
        try:
            diff = await GitHubClient().fetch_pr_diff(body.repo, body.pr_number)  # type: ignore[arg-type]
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch PR diff from GitHub: {exc.response.status_code}",
            ) from exc
    if not diff.strip():
        raise HTTPException(status_code=422, detail="Empty diff; nothing to review.")
    return diff


@router.post("/reviews", response_model=ReviewResponse, status_code=201)
async def create_review(
    body: ReviewRequest,
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    """Run a multi-agent review synchronously and return the full result."""
    diff = await _resolve_diff(body)
    service = ReviewService(session)
    result = await service.run_review(repo=body.repo, pr_number=body.pr_number, diff=diff)
    return ReviewResponse(**result)


@router.post("/reviews/async", response_model=ReviewJob, status_code=202)
async def create_review_async(
    body: ReviewRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReviewJob:
    """Kick off a review in the background and return a streamable job handle.

    Subscribe to ``GET /api/v1/reviews/{id}/stream`` to watch the agents work in
    real time, then ``GET /api/v1/reviews/{id}`` for the persisted result.
    """
    diff = await _resolve_diff(body)
    service = ReviewService(session)
    review_id = await service.create_running(repo=body.repo, pr_number=body.pr_number)

    request.app.state.job_manager.submit(
        review_id, repo=body.repo, pr_number=body.pr_number, diff=diff
    )
    rid = str(review_id)
    return ReviewJob(
        id=rid,
        repo=body.repo,
        pr_number=body.pr_number,
        status="running",
        stream_url=f"/api/v1/reviews/{rid}/stream",
    )


@router.get("/reviews/{review_id}/stream")
async def stream_review(review_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream of a review's progress."""
    broker = request.app.state.broker

    async def event_source() -> AsyncIterator[bytes]:
        queue = await broker.subscribe(review_id)
        # Prompt clients to retry after 3s if the connection drops.
        yield b"retry: 3000\n\n"
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield b": keep-alive\n\n"  # comment frame keeps proxies happy
                    if broker.is_done(review_id):
                        break
                    continue
                payload = json.dumps(event)
                frame = f"event: {event['type']}\ndata: {payload}\n\n"
                yield frame.encode()
                if event.get("type") in ("review.completed", "review.failed"):
                    break
        finally:
            broker.unsubscribe(review_id, queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


@router.get("/reviews", response_model=list[ReviewResponse])
async def list_reviews(
    repo: str | None = Query(None),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewResponse]:
    service = ReviewService(session)
    rows = await service.list_reviews(repo=repo, limit=limit)
    return [ReviewResponse(**r) for r in rows]


@router.get("/reviews/{review_id}", response_model=ReviewResponse)
async def get_review(
    review_id: str,
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    service = ReviewService(session)
    result = await service.get_review(review_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    return ReviewResponse(**result)


@router.post("/reviews/{review_id}/approve", response_model=ReviewResponse)
async def approve_review(
    review_id: str,
    body: ApprovalRequest,
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    """Human-in-the-loop gate: approve or reject a review that requires sign-off."""
    service = ReviewService(session)
    result = await service.approve_review(review_id, body.approved)
    if result is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    return ReviewResponse(**result)


@router.get("/meta")
async def meta() -> dict:
    """Service metadata for the web console (mirrors the root endpoint)."""
    from app import __version__

    settings = get_settings()
    return {
        "name": "CodeSage",
        "version": __version__,
        "llm_mode": "live" if settings.has_llm else "stub",
        "model": settings.model,
        "hitl_threshold": settings.hitl_threshold,
    }


@router.get("/telemetry")
async def telemetry(session: AsyncSession = Depends(get_session)) -> dict:
    """Aggregate token usage and USD cost across all reviews."""
    service = ReviewService(session)
    return await service.get_telemetry()


@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest_repository(
    body: IngestRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Ingest a local repository path into the pgvector RAG index."""
    result = await ingest_path(
        session, root=body.path, repo=body.repo, replace=body.replace
    )
    return IngestResponse(**result)


# ── Eval / regression harness ──────────────────────────────────────────────────
@router.get("/eval/runs")
async def list_eval_runs() -> list[dict]:
    """List saved eval-harness results (runs and cross-version comparisons)."""
    return eval_store.list_results()


@router.post("/eval/runs", status_code=202)
async def launch_eval_run(body: EvalRunRequest) -> dict:
    model = body.model or get_settings().model
    eval_store.launch_run(body.dataset, model)
    return {"status": "scheduled", "dataset": body.dataset, "model": model}


@router.post("/eval/compare", status_code=202)
async def launch_eval_compare(body: EvalCompareRequest) -> dict:
    eval_store.launch_compare(body.dataset, body.baseline, body.candidate, body.tolerance)
    return {
        "status": "scheduled",
        "dataset": body.dataset,
        "baseline": body.baseline,
        "candidate": body.candidate,
    }
