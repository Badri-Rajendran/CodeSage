"""REST API routes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ApprovalRequest,
    IngestRequest,
    IngestResponse,
    ReviewRequest,
    ReviewResponse,
)
from app.db.session import get_session
from app.github.client import GitHubClient
from app.rag.pipeline import ingest_path
from app.services.review_service import ReviewService

router = APIRouter(prefix="/api/v1", tags=["codesage"])


@router.post("/reviews", response_model=ReviewResponse, status_code=201)
async def create_review(
    body: ReviewRequest,
    session: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    """Run a multi-agent review for a PR diff (or fetch the diff from GitHub)."""
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

    service = ReviewService(session)
    result = await service.run_review(repo=body.repo, pr_number=body.pr_number, diff=diff)
    return ReviewResponse(**result)


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
