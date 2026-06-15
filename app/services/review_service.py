"""Review orchestration service.

Runs the LangGraph review pipeline for one PR, persists the review, its findings,
and per-call token-cost telemetry, and returns a structured result.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import build_review_graph, make_deps
from app.agents.tools import ReviewTools
from app.config import get_settings
from app.db.models import Review, UsageEventRow
from app.db.session import SessionFactory
from app.llm.client import LLMClient
from app.llm.telemetry import CostTracker
from app.logging_config import get_logger

logger = get_logger(__name__)


class ReviewService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def run_review(self, *, repo: str, pr_number: int | None, diff: str) -> dict:
        tracker = CostTracker()
        client = LLMClient(self.settings, tracker)
        # RAG retrieval uses its own short-lived sessions (the reviewers run in
        # parallel); the request session is reserved for the sequential writes below.
        tools = ReviewTools(
            repo, diff, settings=self.settings, session_factory=SessionFactory
        )
        deps = make_deps(client, tools, self.settings)
        graph = build_review_graph(deps)

        final_state = await graph.ainvoke(
            {"repo": repo, "pr_number": pr_number, "diff": diff}
        )

        review_id = uuid.uuid4()
        findings = final_state.get("findings", [])
        review = Review(
            id=review_id,
            repo=repo,
            pr_number=pr_number,
            status="completed",
            model=self.settings.model,
            findings=findings,
            summary=final_state.get("summary", ""),
            judge_score=final_state.get("judge_score"),
            judge_rationale=final_state.get("judge_rationale", ""),
            requires_human_approval=final_state.get("requires_human_approval", False),
            approved=None,
        )
        self.session.add(review)

        for ev in tracker.events:
            self.session.add(
                UsageEventRow(
                    review_id=review_id,
                    component=ev.component,
                    model=ev.model,
                    input_tokens=ev.input_tokens,
                    output_tokens=ev.output_tokens,
                    cache_read_tokens=ev.cache_read_tokens,
                    cache_write_tokens=ev.cache_write_tokens,
                    cost_usd=ev.cost,
                )
            )
        await self.session.commit()

        logger.info(
            "Review %s for %s#%s: %d findings, judge=%.2f, hitl=%s, cost=$%.4f",
            review_id,
            repo,
            pr_number,
            len(findings),
            final_state.get("judge_score", 0.0),
            final_state.get("requires_human_approval"),
            tracker.total_cost,
        )

        return {
            "id": str(review_id),
            "repo": repo,
            "pr_number": pr_number,
            "findings": findings,
            "summary": final_state.get("summary", ""),
            "judge_score": final_state.get("judge_score"),
            "judge_rationale": final_state.get("judge_rationale", ""),
            "judge_dimensions": final_state.get("judge_dimensions", {}),
            "requires_human_approval": final_state.get("requires_human_approval", False),
            "approved": None,
            "traces": final_state.get("traces", []),
            "telemetry": tracker.summary(),
        }

    async def get_review(self, review_id: str) -> dict | None:
        review = await self.session.get(Review, uuid.UUID(review_id))
        if review is None:
            return None
        return _review_to_dict(review)

    async def approve_review(self, review_id: str, approved: bool) -> dict | None:
        review = await self.session.get(Review, uuid.UUID(review_id))
        if review is None:
            return None
        review.approved = approved
        review.requires_human_approval = False
        await self.session.commit()
        return _review_to_dict(review)

    async def list_reviews(self, *, repo: str | None = None, limit: int = 50) -> list[dict]:
        stmt = select(Review).order_by(Review.created_at.desc()).limit(limit)
        if repo:
            stmt = stmt.where(Review.repo == repo)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_review_to_dict(r) for r in rows]


def _review_to_dict(r: Review) -> dict:
    return {
        "id": str(r.id),
        "repo": r.repo,
        "pr_number": r.pr_number,
        "status": r.status,
        "model": r.model,
        "findings": r.findings,
        "summary": r.summary,
        "judge_score": r.judge_score,
        "judge_rationale": r.judge_rationale,
        "requires_human_approval": r.requires_human_approval,
        "approved": r.approved,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
