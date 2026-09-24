"""Plain-code helpers for findings: ids, attribution, anchoring, ordering."""

from __future__ import annotations

from app.agents.schemas import SEVERITY_RANK
from app.diff import Diff

_ID_PREFIX = {"security": "sec", "correctness": "cor", "style": "sty"}

FINDING_FIELDS = (
    "category", "severity", "confidence", "path", "line", "end_line",
    "title", "rationale", "suggestion", "evidence",
)


def attribute(drafts: list[dict], reviewer: str) -> list[dict]:
    """Give each draft an id and its reviewer. Code does this, never the model."""
    prefix = _ID_PREFIX.get(reviewer, reviewer[:3])
    out = []
    for i, d in enumerate(drafts, start=1):
        f = {k: d.get(k) for k in FINDING_FIELDS}
        f["category"] = f["category"] or reviewer
        f["evidence"] = list(f["evidence"] or [])
        f["confidence"] = max(0.0, min(1.0, float(f["confidence"] or 0.0)))
        f.update(id=f"{prefix}-{i}", reviewer=reviewer, merged_from=[])
        out.append(f)
    return out


def anchor_and_sort(findings: list[dict], diff: Diff) -> list[dict]:
    """Clear lines GitHub can't comment on, then order by severity and confidence.

    A finding keeps ``line`` only if (path, line) is an added or context line in
    the diff; otherwise it is published in the review body instead of inline.
    """
    for f in findings:
        if not diff.is_commentable(f.get("path"), f.get("line")):
            f["line"] = f["end_line"] = None
        elif f.get("end_line") is not None and (
            f["end_line"] <= f["line"] or not diff.is_commentable(f["path"], f["end_line"])
        ):
            f["end_line"] = None
    return sorted(
        findings,
        key=lambda f: (-SEVERITY_RANK.get(f.get("severity", "info"), 0),
                       -float(f.get("confidence") or 0)),
    )
