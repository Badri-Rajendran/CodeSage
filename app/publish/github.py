"""Publish a review to a pull request: one COMMENT review, and (Action only)
the ``CodeSage`` check run that carries the pass/fail verdict."""

from __future__ import annotations

from typing import Any

import httpx

from app.github.client import GitHubClient
from app.logging_config import get_logger
from app.publish.markdown import (
    CHECK_TEXT_LIMIT,
    comment_body,
    gate_outcome,
    review_body,
    review_marker,
    traces_markdown,
    truncate,
)

logger = get_logger(__name__)

CHECK_NAME = "CodeSage"
MAX_INLINE_COMMENTS = 30  # our own cap; GitHub documents no maximum
MAX_ANNOTATIONS = 50
_LEVEL = {"critical": "failure", "high": "failure", "medium": "warning"}


def _inline_comment(f: dict) -> dict[str, Any]:
    c: dict[str, Any] = {"path": f["path"], "side": "RIGHT", "body": comment_body(f)}
    if f.get("end_line"):  # a range: GitHub wants start_line < line
        c.update(start_line=f["line"], start_side="RIGHT", line=f["end_line"])
    else:
        c["line"] = f["line"]
    return c


class GitHubPublisher:
    def __init__(self, client: GitHubClient, repo: str, pr_number: int, head_sha: str):
        self.client = client
        self.repo = repo
        self.pr_number = pr_number
        self.head_sha = head_sha

    async def post_review(
        self,
        state: dict,
        telemetry: dict,
        *,
        review_id: str | None = None,
        skipped_files: list[str] | None = None,
    ) -> str | None:
        """Post the review; return its URL. With ``review_id``, posting is idempotent:
        if a review carrying that id's marker already exists, it is not posted again."""
        if review_id:
            marker = review_marker(review_id)
            for r in await self.client.list_reviews(self.repo, self.pr_number):
                if marker in (r.get("body") or ""):
                    logger.info("Review %s already posted; skipping.", review_id)
                    return r.get("html_url")
        anchored = [f for f in state.get("findings", []) if f.get("path") and f.get("line")]
        inline = anchored[:MAX_INLINE_COMMENTS]
        inline_ids = {f["id"] for f in inline}
        body = review_body(state, telemetry, inline_ids=inline_ids,
                           skipped_files=skipped_files, review_id=review_id)
        try:
            resp = await self.client.create_review(
                self.repo, self.pr_number, commit_id=self.head_sha, body=body,
                comments=[_inline_comment(f) for f in inline],
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422 or not inline:
                raise
            # GitHub rejected an inline comment (e.g. a line outside the diff):
            # retry once with every finding in the body.
            logger.warning("Inline review rejected (422: %s); posting body-only.",
                           exc.response.text[:300])
            body = review_body(state, telemetry, skipped_files=skipped_files,
                               review_id=review_id)
            resp = await self.client.create_review(
                self.repo, self.pr_number, commit_id=self.head_sha, body=body
            )
        return resp.get("html_url")

    async def start_check(self) -> int:
        run = await self.client.create_check_run(
            self.repo, name=CHECK_NAME, head_sha=self.head_sha,
            title="Reviewing…", summary="CodeSage agents are reviewing this pull request.",
        )
        return int(run["id"])

    async def finish_check(
        self,
        check_run_id: int,
        state: dict,
        telemetry: dict,
        *,
        error: str | None = None,
        skipped_files: list[str] | None = None,
    ) -> str:
        """Complete the check run; return its conclusion."""
        conclusion, title = gate_outcome(state, error)
        if error:
            summary, text, annotations = f"CodeSage failed: {error}", None, []
        else:
            summary = review_body(state, telemetry, skipped_files=skipped_files)
            text = traces_markdown(state)
            annotations = [
                {
                    "path": f["path"],
                    "start_line": f["line"],
                    "end_line": f.get("end_line") or f["line"],
                    "annotation_level": _LEVEL.get(f["severity"], "notice"),
                    "title": truncate(f["title"], 255),
                    "message": truncate(f["rationale"], 64_000),
                }
                for f in state.get("findings", [])
                if f.get("path") and f.get("line")
            ][:MAX_ANNOTATIONS]
        await self.client.update_check_run(
            self.repo, check_run_id, conclusion=conclusion, title=title,
            summary=truncate(summary, CHECK_TEXT_LIMIT),
            text=truncate(text, CHECK_TEXT_LIMIT) if text else None,
            annotations=annotations,
        )
        return conclusion
