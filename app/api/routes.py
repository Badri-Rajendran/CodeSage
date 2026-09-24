"""REST API routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_key
from app.api.schemas import (
    DecisionRequest,
    EvalCompareRequest,
    EvalRunRequest,
    IngestRequest,
    IngestResponse,
    ReviewJob,
    ReviewRequest,
    ReviewResponse,
)
from app.config import get_settings
from app.db.session import SessionFactory, get_session
from app.eval import store as eval_store
from app.github.client import GitHubClient
from app.rag.pipeline import IngestPathError, ingest_path, resolve_ingest_root
from app.realtime.broker import END_OF_STREAM
from app.services.review_service import (
    ReviewConflict,
    ReviewNotFound,
    ReviewService,
    parse_review_id,
)

# Every route on `router` requires an API key; `public_router` is unauthenticated
# and must only expose non-sensitive service metadata.
router = APIRouter(
    prefix="/api/v1", tags=["codesage"], dependencies=[Depends(require_api_key)]
)
public_router = APIRouter(prefix="/api/v1", tags=["meta"])


async def _resolve_diff(body: ReviewRequest) -> str:
    """Return the diff from the request, or fetch it from GitHub by PR number."""
    diff = body.diff
    if not diff:
        try:
            diff = await GitHubClient().fetch_pr_diff(body.repo, body.pr_number)  # type: ignore[arg-type]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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

    Given only a PR number (no diff), the review runs in PR mode: agents read a
    clone of the PR head, and the review is posted to the PR when the gate passes
    or a human approves it. Given a diff, nothing is posted.
    """
    diff = await _resolve_diff(body)
    service = ReviewService(session)
    review_id = await service.create_running(repo=body.repo, pr_number=body.pr_number)

    request.app.state.job_manager.submit(
        review_id, repo=body.repo, pr_number=body.pr_number, diff=diff,
        pr_mode=body.diff is None and body.pr_number is not None,
    )
    return _job(str(review_id), body.repo, body.pr_number)


def _job(rid: str, repo: str, pr_number: int | None) -> ReviewJob:
    return ReviewJob(id=rid, repo=repo, pr_number=pr_number, status="running",
                     stream_url=f"/api/v1/reviews/{rid}/stream")


def _final_event(review: dict) -> dict:
    """The event a finished or paused review's stream ends with, rebuilt from the DB."""
    rid, status = review["id"], review["status"]
    if status == "failed":
        return {"type": "review.failed", "review_id": rid,
                "error": review.get("error") or "review failed"}
    if status == "awaiting_approval":
        return {"type": "review.awaiting_approval", "review_id": rid,
                "gate_reasons": review["gate_reasons"], "judge_score": review["judge_score"],
                "review": review}
    return {"type": "review.completed", "review_id": rid, "review": review}


@router.get("/reviews/{review_id}/stream")
async def stream_review(review_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream of a review's progress.

    Unknown reviews get 404. A review that already finished (or is waiting for
    approval) and is no longer in the in-memory broker, for example after a
    restart, gets its final state from the database, then the stream closes.
    """
    broker = request.app.state.broker
    async with SessionFactory() as session:
        review = await ReviewService(session).get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    replay_from_db = not broker.has_history(review["id"]) and review["status"] != "running"

    async def event_source() -> AsyncIterator[bytes]:
        yield b"retry: 3000\n\n"  # prompt clients to retry after 3s if the connection drops
        if replay_from_db:
            yield _frame(_final_event(review))
            return
        queue = await broker.subscribe(review["id"])
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield b": keep-alive\n\n"  # comment frame keeps proxies happy
                    if broker.is_done(review["id"]):
                        break
                    continue
                yield _frame(event)
                if event.get("type") in END_OF_STREAM:
                    break
        finally:
            broker.unsubscribe(review["id"], queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


def _frame(event: dict) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


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


@router.post("/reviews/{review_id}/decision", response_model=ReviewJob, status_code=202)
async def decide_review(
    review_id: str,
    body: DecisionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReviewJob:
    """Human-in-the-loop gate: approve (post to the PR) or reject a paused review.

    409 unless the review is awaiting approval. The review then resumes in the
    background; follow it on the same stream URL.
    """
    service = ReviewService(session)
    try:
        review = await service.decide(review_id, approved=body.approved, note=body.note)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail="Review not found.") from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    request.app.state.job_manager.submit_resume(
        parse_review_id(review["id"]), approved=body.approved, note=body.note
    )
    return _job(review["id"], review["repo"], review["pr_number"])


@public_router.get("/meta")
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
        "auth_required": not settings.auth_disabled,
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
    """Ingest a server-side repository path into the pgvector RAG index.

    Only paths under ``CODESAGE_INGEST_ROOTS`` are accepted.
    """
    try:
        root = resolve_ingest_root(body.path, get_settings().ingest_root_list)
    except IngestPathError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    result = await ingest_path(session, root=str(root), repo=body.repo, replace=body.replace)
    return IngestResponse(**result)


# ── Eval / regression harness ──────────────────────────────────────────────────
def _dataset_path(dataset: str) -> str:
    try:
        return str(eval_store.resolve_dataset(dataset))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/eval/runs")
async def list_eval_runs() -> list[dict]:
    """List saved eval-harness results (runs and cross-version comparisons)."""
    return eval_store.list_results()


@router.post("/eval/runs", status_code=202)
async def launch_eval_run(body: EvalRunRequest) -> dict:
    model = body.model or get_settings().model
    eval_store.launch_run(_dataset_path(body.dataset), model)
    return {"status": "scheduled", "dataset": body.dataset, "model": model}


@router.post("/eval/compare", status_code=202)
async def launch_eval_compare(body: EvalCompareRequest) -> dict:
    eval_store.launch_compare(
        _dataset_path(body.dataset), body.baseline, body.candidate, body.tolerance
    )
    return {
        "status": "scheduled",
        "dataset": body.dataset,
        "baseline": body.baseline,
        "candidate": body.candidate,
    }
