"""Review orchestration service.

Runs the LangGraph review pipeline for one PR, persists the review, its findings,
and per-call token-cost telemetry, and returns a structured result.

Two execution paths share one graph runner (``_execute_graph``):

* ``run_review`` — synchronous; runs the graph to completion and persists a new
  review row. Used by the REST ``POST /reviews`` endpoint and the test suite.
* ``run_review_streamed`` — drives the same graph with ``astream`` and publishes
  a progress event per node so the UI can render a live timeline; it updates a
  pre-inserted "running" row to "completed". Used by the async job manager.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import STAGES, build_review_graph, make_deps, prepare_diff
from app.agents.state import ADDITIVE_KEYS, OR_KEYS
from app.config import get_settings
from app.db.models import Review, UsageEventRow
from app.db.session import SessionFactory
from app.llm.client import LLMClient
from app.llm.telemetry import CostTracker
from app.logging_config import get_logger
from app.rag.search import pgvector_search
from app.review_config import default_review_config
from app.workspace import Workspace

logger = get_logger(__name__)

# Graph node order. The first three run in parallel; `revise` runs only when the
# judge asks for it. The UI uses this plus its own stage dependency map
# (web/src/lib/useReviewStream.ts) to derive running/pending/skipped states.
STAGE_ORDER = list(STAGES)

Publisher = Callable[[dict[str, Any]], Awaitable[None]]


class ReviewService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    # ── Graph execution (shared) ───────────────────────────────────────────────
    async def _execute_graph(
        self,
        *,
        review_id: uuid.UUID,
        repo: str,
        pr_number: int | None,
        diff: str,
        publish: Publisher | None = None,
    ) -> tuple[dict, CostTracker]:
        """Run the review graph, optionally streaming per-stage progress events."""
        tracker = CostTracker()
        client = LLMClient(self.settings, tracker)
        cfg = default_review_config(self.settings)
        # Semantic search opens its own short-lived sessions (the reviewers run in
        # parallel); the request session is reserved for the sequential writes.
        workspace = Workspace.diff_only(
            prepare_diff(diff, cfg), semantic=pgvector_search(SessionFactory, repo)
        )
        graph = build_review_graph(make_deps(client, workspace, self.settings, cfg=cfg))

        rid = str(review_id)
        inputs = {"repo": repo, "pr_number": pr_number, "diff": diff}
        final_state: dict[str, Any] = dict(inputs)

        if publish is not None:
            await publish(
                {
                    "type": "review.started",
                    "review_id": rid,
                    "repo": repo,
                    "pr_number": pr_number,
                    "model": self.settings.model,
                    "stages": STAGE_ORDER,
                }
            )

        # ``updates`` mode yields {node_name: state_delta} as each node finishes.
        async for update in graph.astream(inputs, stream_mode="updates"):
            for node, delta in update.items():
                self._merge_delta(final_state, delta)
                if publish is not None:
                    await publish(self._stage_event(rid, node, delta, final_state))

        if publish is not None:
            await publish(
                {"type": "telemetry.update", "review_id": rid, "telemetry": tracker.summary()}
            )

        return final_state, tracker

    @staticmethod
    def _merge_delta(state: dict[str, Any], delta: dict[str, Any] | None) -> None:
        """Apply a node's state delta, mirroring the reducers in app/agents/state.py."""
        if not delta:
            return
        for key, value in delta.items():
            if key in ADDITIVE_KEYS:
                state.setdefault(key, []).extend(value or [])
            elif key in OR_KEYS:
                state[key] = bool(state.get(key)) or bool(value)
            else:
                state[key] = value

    @staticmethod
    def _stage_event(
        rid: str, node: str, delta: dict[str, Any] | None, state: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the ``stage.completed`` event payload for one finished node."""
        delta = delta or {}
        payload: dict[str, Any] = {"type": "stage.completed", "review_id": rid, "stage": node}
        if node in ("security", "correctness", "style"):
            traces = delta.get("traces") or []
            payload["findings_count"] = len(delta.get("draft_findings") or [])
            payload["trace"] = traces[0] if traces else None
        elif node in ("reflection", "revise"):
            payload["findings_count"] = len(state.get("findings", []))
            payload["summary"] = state.get("summary", "")
            payload["trace"] = state.get("reflection_trace")
        elif node == "judge":
            payload["judge_score"] = state.get("judge_score")
            payload["judge_dimensions"] = state.get("judge_dimensions", {})
            payload["judge_rationale"] = state.get("judge_rationale", "")
        elif node == "human_gate":
            payload["requires_human_approval"] = state.get("requires_human_approval", False)
            payload["gate_reasons"] = state.get("gate_reasons", [])
        elif node == "publish":
            payload["github_review_url"] = state.get("github_review_url")
        return payload

    def _result_dict(
        self,
        review_id: uuid.UUID,
        repo: str,
        pr_number: int | None,
        state: dict[str, Any],
        tracker: CostTracker,
        *,
        approved: bool | None = None,
        requires_human_approval: bool | None = None,
    ) -> dict:
        return {
            "id": str(review_id),
            "repo": repo,
            "pr_number": pr_number,
            "findings": state.get("findings", []),
            "summary": state.get("summary", ""),
            "judge_score": state.get("judge_score"),
            "judge_rationale": state.get("judge_rationale", ""),
            "judge_dimensions": state.get("judge_dimensions", {}),
            "requires_human_approval": (
                state.get("requires_human_approval", False)
                if requires_human_approval is None
                else requires_human_approval
            ),
            "approved": approved,
            "traces": state.get("traces", []),
            "telemetry": tracker.summary(),
            "gate_reasons": state.get("gate_reasons", []),
            "budget_limited": bool(state.get("budget_limited")),
            "revision_count": state.get("revision_count", 0),
            "github_review_url": state.get("github_review_url"),
        }

    def _record_usage(self, review_id: uuid.UUID, tracker: CostTracker) -> None:
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

    # ── Synchronous path (REST POST /reviews, tests) ───────────────────────────
    async def run_review(self, *, repo: str, pr_number: int | None, diff: str) -> dict:
        review_id = uuid.uuid4()
        final_state, tracker = await self._execute_graph(
            review_id=review_id, repo=repo, pr_number=pr_number, diff=diff
        )

        self.session.add(
            Review(
                id=review_id,
                repo=repo,
                pr_number=pr_number,
                status="completed",
                model=self.settings.model,
                findings=final_state.get("findings", []),
                summary=final_state.get("summary", ""),
                judge_score=final_state.get("judge_score"),
                judge_rationale=final_state.get("judge_rationale", ""),
                requires_human_approval=final_state.get("requires_human_approval", False),
                approved=None,
            )
        )
        self._record_usage(review_id, tracker)
        await self.session.commit()

        logger.info(
            "Review %s for %s#%s: %d findings, judge=%.2f, hitl=%s, cost=$%.4f",
            review_id,
            repo,
            pr_number,
            len(final_state.get("findings", [])),
            final_state.get("judge_score") or 0.0,
            final_state.get("requires_human_approval"),
            tracker.total_cost,
        )
        return self._result_dict(review_id, repo, pr_number, final_state, tracker)

    # ── Streaming path (async job manager) ─────────────────────────────────────
    async def create_running(
        self, *, repo: str, pr_number: int | None
    ) -> uuid.UUID:
        """Insert a placeholder 'running' review row and return its id."""
        review_id = uuid.uuid4()
        self.session.add(
            Review(
                id=review_id,
                repo=repo,
                pr_number=pr_number,
                status="running",
                model=self.settings.model,
                findings=[],
                summary="",
                requires_human_approval=False,
                approved=None,
            )
        )
        await self.session.commit()
        return review_id

    async def run_review_streamed(
        self,
        review_id: uuid.UUID,
        *,
        repo: str,
        pr_number: int | None,
        diff: str,
        publish: Publisher,
    ) -> dict:
        """Run the graph with live progress, updating the pre-inserted row."""
        final_state, tracker = await self._execute_graph(
            review_id=review_id, repo=repo, pr_number=pr_number, diff=diff, publish=publish
        )

        review = await self.session.get(Review, review_id)
        if review is None:  # row was never created (defensive) — create it now.
            review = Review(id=review_id, repo=repo, pr_number=pr_number)
            self.session.add(review)
        review.status = "completed"
        review.model = self.settings.model
        review.findings = final_state.get("findings", [])
        review.summary = final_state.get("summary", "")
        review.judge_score = final_state.get("judge_score")
        review.judge_rationale = final_state.get("judge_rationale", "")
        review.requires_human_approval = final_state.get("requires_human_approval", False)
        self._record_usage(review_id, tracker)
        await self.session.commit()

        logger.info(
            "Streamed review %s for %s#%s: %d findings, judge=%.2f, cost=$%.4f",
            review_id,
            repo,
            pr_number,
            len(final_state.get("findings", [])),
            final_state.get("judge_score") or 0.0,
            tracker.total_cost,
        )

        result = self._result_dict(review_id, repo, pr_number, final_state, tracker)
        await publish({"type": "review.completed", "review_id": str(review_id), "review": result})
        return result

    async def mark_failed(self, review_id: uuid.UUID, error: str) -> None:
        review = await self.session.get(Review, review_id)
        if review is not None:
            review.status = "failed"
            review.summary = f"Review failed: {error}"
            await self.session.commit()

    # ── Reads ──────────────────────────────────────────────────────────────────
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

    async def get_telemetry(self) -> dict:
        """Aggregate token usage and cost across all persisted reviews."""
        totals = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(UsageEventRow.cost_usd), 0.0),
                    func.coalesce(func.sum(UsageEventRow.input_tokens), 0),
                    func.coalesce(func.sum(UsageEventRow.output_tokens), 0),
                    func.count(UsageEventRow.id),
                )
            )
        ).one()

        by_component_rows = (
            await self.session.execute(
                select(
                    UsageEventRow.component,
                    func.coalesce(func.sum(UsageEventRow.cost_usd), 0.0),
                    func.coalesce(func.sum(UsageEventRow.input_tokens), 0),
                    func.coalesce(func.sum(UsageEventRow.output_tokens), 0),
                    func.count(UsageEventRow.id),
                ).group_by(UsageEventRow.component)
            )
        ).all()

        n_reviews = (
            await self.session.execute(select(func.count(Review.id)))
        ).scalar_one()

        return {
            "total_cost_usd": round(float(totals[0]), 6),
            "total_input_tokens": int(totals[1]),
            "total_output_tokens": int(totals[2]),
            "total_calls": int(totals[3]),
            "total_reviews": int(n_reviews),
            "by_component": [
                {
                    "component": row[0],
                    "cost_usd": round(float(row[1]), 6),
                    "input_tokens": int(row[2]),
                    "output_tokens": int(row[3]),
                    "calls": int(row[4]),
                }
                for row in sorted(by_component_rows, key=lambda r: r[1], reverse=True)
            ],
        }


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
