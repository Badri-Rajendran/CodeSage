"""When does the Action review a pull request? (design 02, "Trigger rules")

The consumer workflow's job-level ``if`` applies the same rules; they're
checked again here so a mistake in a copied workflow can't cause unintended
(paid) runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REVIEW_LABEL = "codesage:review"
REVIEW_COMMAND = "/codesage review"
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


@dataclass(frozen=True)
class Trigger:
    run: bool
    reason: str
    pr_number: int | None = None
    head_sha: str | None = None  # None for comments: look it up with GET /pulls/{n}
    label_trigger: bool = False


def evaluate(event_name: str, payload: dict[str, Any]) -> Trigger:
    action = payload.get("action")
    if event_name == "pull_request":
        pr = payload.get("pull_request") or {}
        number, sha = pr.get("number"), (pr.get("head") or {}).get("sha")
        if action == "opened":
            if pr.get("draft"):
                return Trigger(False, "draft pull request (reviewed when marked ready)")
            return Trigger(True, "pull request opened", number, sha)
        if action == "ready_for_review":
            return Trigger(True, "pull request marked ready for review", number, sha)
        if action == "labeled":
            if (payload.get("label") or {}).get("name") == REVIEW_LABEL:
                return Trigger(True, f"'{REVIEW_LABEL}' label added", number, sha,
                               label_trigger=True)
            return Trigger(False, "a different label was added")
        return Trigger(False, f"pull_request '{action}' does not trigger a review")

    if event_name == "issue_comment":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        if action != "created":
            return Trigger(False, "only new comments trigger a review")
        if issue.get("pull_request") is None:
            return Trigger(False, "comment is on an issue, not a pull request")
        if not (comment.get("body") or "").strip().startswith(REVIEW_COMMAND):
            return Trigger(False, f"comment does not start with '{REVIEW_COMMAND}'")
        if comment.get("author_association") not in TRUSTED_ASSOCIATIONS:
            return Trigger(False, "comment author is not an owner, member or collaborator")
        return Trigger(True, f"'{REVIEW_COMMAND}' requested", issue.get("number"))

    return Trigger(False, f"event '{event_name}' does not trigger a review")


def is_fork(pr: dict[str, Any], repository: str) -> bool:
    """Fork PRs are skipped: their head repo differs (or was deleted)."""
    head_repo = (pr.get("head") or {}).get("repo") or {}
    return head_repo.get("full_name") != repository
