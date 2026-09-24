"""Review orchestration service (local mode: API, console, jobs).

Runs the review graph for one PR or diff, streams per-stage progress events,
persists the review with its findings, traces, telemetry and gate history, and
(in PR mode) posts the review to GitHub.

Execution paths, all sharing ``_engine`` + ``_drive``:

* ``run_review``: synchronous, ``report`` mode (never pauses). REST ``POST /reviews``.
* ``run_review_streamed``: the async job. With a checkpointer the graph runs in
  ``local`` mode: a tripped gate *pauses* it (``interrupt()``), the row becomes
  ``awaiting_approval``, and the checkpoint survives restarts.
* ``resume_streamed``: continues a paused review with the human's decision.
  Approve posts the review to the PR; reject posts nothing.

PR mode (a PR number, no explicit diff, and ``GITHUB_TOKEN`` set) reviews a
temporary clone at the PR head and can post. Diff mode reviews the diff alone
and never posts.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import STAGES, build_review_graph, make_deps, prepare_diff
from app.agents.state import ADDITIVE_KEYS, OR_KEYS
from app.config import get_settings
from app.db.models import Review, UsageEventRow
from app.db.session import SessionFactory
from app.diff import Diff
from app.github.client import GitHubClient
from app.llm.client import LLMClient
from app.llm.telemetry import CostTracker
from app.logging_config import get_logger
from app.publish.github import GitHubPublisher
from app.rag.search import pgvector_search
from app.review_config import default_review_config
from app.workspace import Workspace

logger = get_logger(__name__)

# Graph node order. The first three run in parallel; `revise` runs only when the
# judge asks for it. The UI uses this plus its own stage dependency map
# (web/src/lib/useReviewStream.ts) to derive running/pending/skipped states.
STAGE_ORDER = list(STAGES)

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class ReviewNotFound(LookupError):
    pass


class ReviewConflict(RuntimeError):
    pass


def parse_review_id(review_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(review_id)
    except ValueError as exc:
        raise ReviewNotFound(review_id) from exc


def _thread(review_id: uuid.UUID) -> dict:
    return {"configurable": {"thread_id": str(review_id)}}


def combine_telemetry(prior: dict | None, now: dict) -> dict:
    """Telemetry from before a pause (persisted) plus the resumed run's."""
    if not prior:
        return now
    events = [*prior.get("by_component", []), *now.get("by_component", [])]
    return {
        "total_cost_usd": round(prior.get("total_cost_usd", 0) + now.get("total_cost_usd", 0), 6),
        "total_input_tokens": prior.get("total_input_tokens", 0)
        + now.get("total_input_tokens", 0),
        "total_output_tokens": prior.get("total_output_tokens", 0)
        + now.get("total_output_tokens", 0),
        "calls": len(events),
        "by_component": events,
    }


class ReviewService:
    def __init__(self, session: AsyncSession, checkpointer: Any | None = None):
        self.session = session
        self.checkpointer = checkpointer
        self.settings = get_settings()

    # ── Engine assembly ───────────────────────────────────────────────────────
    def _publisher(self, review_id: uuid.UUID, tracker: CostTracker, prior: dict | None):
        """Posts to the PR when there is one and the gate passed or was approved.
        Idempotent: the review carries a marker with the review id."""

        async def publish(state: dict) -> dict:
            pr = state.get("pr")
            if not pr or not self.settings.github_token:
                return {}
            if state.get("gate_tripped") and state.get("decision") != "approved":
                return {}
            publisher = GitHubPublisher(
                GitHubClient(self.settings), state["repo"], pr["number"], pr["head_sha"]
            )
            url = await publisher.post_review(
                state, combine_telemetry(prior, tracker.summary()), review_id=str(review_id)
            )
            return {"github_review_url": url}

        return publish

    @asynccontextmanager
    async def _engine(
        self,
        review_id: uuid.UUID,
        *,
        repo: str,
        diff: str,
        pr: dict | None,
        tracker: CostTracker,
        mode: str,
        prior_telemetry: dict | None = None,
        clone: bool = False,
    ) -> AsyncIterator[Any]:
        """Yield a compiled graph; in PR mode, over a temporary clone at the head."""
        cfg = default_review_config(self.settings)
        client = LLMClient(self.settings, tracker)
        # Semantic search opens its own short-lived sessions (the reviewers run in
        # parallel); the request session is reserved for the sequential writes.
        semantic = pgvector_search(SessionFactory, repo)
        publisher = self._publisher(review_id, tracker, prior_telemetry)
        checkpointer = self.checkpointer if mode == "local" else None

        def graph_for(workspace: Workspace):
            deps = make_deps(client, workspace, self.settings, cfg=cfg, publisher=publisher)
            return build_review_graph(deps, mode=mode, checkpointer=checkpointer)  # type: ignore[arg-type]

        parsed = prepare_diff(diff, cfg) if diff else Diff()
        if clone and pr:
            async with Workspace.clone_at(
                repo, pr["head_sha"], parsed, base_sha=pr["base_sha"],
                token=self.settings.github_token or None, semantic=semantic,
            ) as workspace:
                yield graph_for(workspace)
        else:
            yield graph_for(Workspace.diff_only(parsed, semantic=semantic))

    async def _pr_context(self, repo: str, pr_number: int) -> dict:
        pr = await GitHubClient(self.settings).get_pr(repo, pr_number)
        return {
            "number": pr["number"],
            "title": pr.get("title") or "",
            "body": pr.get("body") or "",
            "base_sha": pr["base"]["sha"],
            "head_sha": pr["head"]["sha"],
            "url": pr.get("html_url"),
        }

    async def _drive(
        self,
        graph: Any,
        graph_input: Any,
        config: dict,
        rid: str,
        emit: EventSink | None,
        state: dict[str, Any],
    ) -> bool:
        """Stream the graph into ``state``; return True if it paused at the gate."""
        paused = False
        async for update_ in graph.astream(graph_input, config, stream_mode="updates"):
            for node, delta in update_.items():
                if node == "__interrupt__":
                    # The gate paused before returning its update; its reasons
                    # travel in the interrupt payload.
                    paused = True
                    for pending in delta or ():
                        value = getattr(pending, "value", None) or {}
                        state["gate_tripped"] = True
                        state["gate_reasons"] = value.get("reasons", [])
                    continue
                self._merge_delta(state, delta)
                if emit is not None:
                    await emit(self._stage_event(rid, node, delta, state))
        return paused

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

    # ── Persistence ────────────────────────────────────────────────────────────
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

    @staticmethod
    def _apply_state(review: Review, state: dict[str, Any], telemetry: dict) -> None:
        pr = state.get("pr") or {}
        review.findings = state.get("findings", [])
        review.summary = state.get("summary", "")
        review.judge_score = state.get("judge_score")
        review.judge_rationale = state.get("judge_rationale", "")
        review.judge_dimensions = state.get("judge_dimensions") or {}
        review.traces = [
            *state.get("traces", []),
            *([state["reflection_trace"]] if state.get("reflection_trace") else []),
        ]
        review.telemetry = telemetry
        review.gate_tripped = bool(state.get("gate_tripped"))
        review.gate_reasons = state.get("gate_reasons", [])
        review.requires_human_approval = review.gate_tripped
        review.budget_limited = bool(state.get("budget_limited"))
        review.revision_count = state.get("revision_count", 0)
        review.github_review_url = state.get("github_review_url")
        if pr:
            review.base_sha, review.head_sha = pr.get("base_sha"), pr.get("head_sha")
            review.pr_title = pr.get("title")

    async def _load(self, review_id: uuid.UUID) -> Review:
        review = await self.session.get(Review, review_id)
        if review is None:
            raise ReviewNotFound(str(review_id))
        return review

    # ── Synchronous path (REST POST /reviews) ─────────────────────────────────
    async def run_review(self, *, repo: str, pr_number: int | None, diff: str) -> dict:
        review_id = uuid.uuid4()
        tracker = CostTracker()
        state: dict[str, Any] = {"repo": repo, "pr_number": pr_number, "diff": diff}
        async with self._engine(
            review_id, repo=repo, diff=diff, pr=None, tracker=tracker, mode="report"
        ) as graph:
            await self._drive(graph, dict(state), {}, str(review_id), None, state)
        review = Review(id=review_id, repo=repo, pr_number=pr_number, status="completed",
                        model=self.settings.model)
        self._apply_state(review, state, tracker.summary())
        self.session.add(review)
        self._record_usage(review_id, tracker)
        await self.session.commit()
        await self.session.refresh(review)
        logger.info("Review %s for %s#%s: %d findings, judge=%s, cost=$%.4f", review_id, repo,
                    pr_number, len(review.findings), review.judge_score, tracker.total_cost)
        return _review_to_dict(review)

    # ── Streaming path (async job manager) ─────────────────────────────────────
    async def create_running(self, *, repo: str, pr_number: int | None) -> uuid.UUID:
        """Insert a placeholder 'running' review row and return its id."""
        review_id = uuid.uuid4()
        self.session.add(
            Review(id=review_id, repo=repo, pr_number=pr_number, status="running",
                   model=self.settings.model, findings=[], summary="")
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
        pr_mode: bool,
        emit: EventSink,
    ) -> dict:
        """Run the graph with live progress; pause at a tripped gate (local mode)."""
        rid = str(review_id)
        tracker = CostTracker()
        pr = None
        if pr_mode and pr_number and self.settings.github_token:
            pr = await self._pr_context(repo, pr_number)
        mode = "local" if self.checkpointer is not None else "report"
        if mode == "report":
            logger.warning("No checkpointer: review %s runs without a pausing gate.", rid)
        await emit({"type": "review.started", "review_id": rid, "repo": repo,
                    "pr_number": pr_number, "model": self.settings.model,
                    "stages": STAGE_ORDER, "pr_mode": pr is not None})
        state: dict[str, Any] = {"repo": repo, "pr_number": pr_number, "diff": diff, "pr": pr}
        async with self._engine(review_id, repo=repo, diff=diff, pr=pr, tracker=tracker,
                                mode=mode, clone=pr is not None) as graph:
            paused = await self._drive(graph, dict(state), _thread(review_id), rid, emit, state)

        review = await self._load(review_id)
        review.status = "awaiting_approval" if paused else "completed"
        review.model = self.settings.model
        self._apply_state(review, state, tracker.summary())
        self._record_usage(review_id, tracker)
        await self.session.commit()
        await self.session.refresh(review)
        result = _review_to_dict(review)
        await emit({"type": "telemetry.update", "review_id": rid,
                    "telemetry": tracker.summary()})
        if paused:
            await emit({"type": "review.awaiting_approval", "review_id": rid,
                        "gate_reasons": review.gate_reasons,
                        "judge_score": review.judge_score, "review": result})
        else:
            await emit({"type": "review.completed", "review_id": rid, "review": result})
        logger.info("Streamed review %s for %s#%s: %s, %d findings, cost=$%.4f", rid, repo,
                    pr_number, review.status, len(review.findings), tracker.total_cost)
        return result

    async def decide(self, review_id: str, *, approved: bool, note: str | None) -> dict:
        """Record the human decision on a paused review; the caller then resumes it."""
        rid = parse_review_id(review_id)
        review = await self._load(rid)
        if review.status != "awaiting_approval":
            raise ReviewConflict(f"review is {review.status}, not awaiting approval")
        review.decision = "approved" if approved else "rejected"
        review.decision_note = note
        review.decided_at = datetime.now(UTC)
        review.status = "running"  # a second decision now gets 409
        await self.session.commit()
        await self.session.refresh(review)
        return _review_to_dict(review)

    async def resume_streamed(
        self, review_id: uuid.UUID, *, approved: bool, note: str | None, emit: EventSink
    ) -> dict:
        """Resume a paused review with the decision; approve posts, reject doesn't."""
        rid = str(review_id)
        if self.checkpointer is None:
            raise RuntimeError("cannot resume: no checkpointer configured")
        review = await self._load(review_id)
        prior = review.telemetry
        tracker = CostTracker()
        async with self._engine(review_id, repo=review.repo, diff="", pr=None,
                                tracker=tracker, mode="local", prior_telemetry=prior) as graph:
            snapshot = await graph.aget_state(_thread(review_id))
            if not snapshot.next:
                raise RuntimeError("no paused checkpoint for this review")
            state: dict[str, Any] = dict(snapshot.values)
            await self._drive(graph, Command(resume={"approved": approved, "note": note}),
                              _thread(review_id), rid, emit, state)

        review.status = "completed" if approved else "rejected"
        self._apply_state(review, state, combine_telemetry(prior, tracker.summary()))
        self._record_usage(review_id, tracker)
        await self.session.commit()
        await self.session.refresh(review)
        result = _review_to_dict(review)
        await emit({"type": "review.completed", "review_id": rid, "review": result})
        return result

    async def mark_failed(self, review_id: uuid.UUID, error: str) -> None:
        review = await self.session.get(Review, review_id)
        if review is not None:
            review.status = "failed"
            review.error = error[:2000]
            await self.session.commit()

    async def reconcile_interrupted(self) -> int:
        """Rows left 'running' by a restart can't finish: mark them failed.
        (Paused reviews stay 'awaiting_approval'; their checkpoints are in Postgres.)"""
        result = await self.session.execute(
            update(Review)
            .where(Review.status == "running")
            .values(status="failed", error="interrupted by restart")
        )
        await self.session.commit()
        return result.rowcount or 0  # type: ignore[attr-defined]

    # ── Reads ──────────────────────────────────────────────────────────────────
    async def get_review(self, review_id: str) -> dict | None:
        try:
            review = await self._load(parse_review_id(review_id))
        except ReviewNotFound:
            return None
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
        "pr_title": r.pr_title,
        "head_sha": r.head_sha,
        "status": r.status,
        "model": r.model,
        "findings": r.findings,
        "summary": r.summary,
        "judge_score": r.judge_score,
        "judge_rationale": r.judge_rationale,
        "judge_dimensions": r.judge_dimensions or {},
        "traces": r.traces or [],
        "telemetry": r.telemetry,
        "gate_tripped": r.gate_tripped,
        "gate_reasons": r.gate_reasons or [],
        "budget_limited": r.budget_limited,
        "revision_count": r.revision_count,
        # Compatibility fields, now derived from the status and decision.
        "requires_human_approval": r.status == "awaiting_approval",
        "approved": None if r.decision is None else r.decision == "approved",
        "decision": r.decision,
        "decision_note": r.decision_note,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "github_review_url": r.github_review_url,
        "error": r.error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
