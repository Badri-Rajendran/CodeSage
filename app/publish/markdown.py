"""Render a finished review as GitHub markdown: review body, inline comments,
check-run title/conclusion, and the Actions job summary."""

from __future__ import annotations

from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version

CHECK_TEXT_LIMIT = 65_535  # check-run output.summary / output.text
STEP_SUMMARY_LIMIT = 1024 * 1024  # $GITHUB_STEP_SUMMARY per step
BUDGET_REASON = "budget-limited: not judged"

SEVERITIES = ("critical", "high", "medium", "low", "info")
BADGE = {
    "critical": "🔴 **Critical**",
    "high": "🟠 **High**",
    "medium": "🟡 **Medium**",
    "low": "🔵 Low",
    "info": "⚪ Info",
}


def codesage_version() -> str:
    try:
        return version("codesage")
    except PackageNotFoundError:
        return "dev"


def review_marker(review_id: str) -> str:
    """Hidden marker that makes posting idempotent (local mode checks for it)."""
    return f"<!-- codesage-review:{review_id} -->"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    note = "\n\n…(truncated)"
    return text[: limit - len(note)] + note


def _location(f: dict) -> str:
    if not f.get("path"):
        return ""
    loc = f["path"]
    if f.get("line"):
        loc += f":{f['line']}" + (f"-{f['end_line']}" if f.get("end_line") else "")
    return f"`{loc}` "


def comment_body(f: dict) -> str:
    """One inline review comment."""
    parts = [f"{BADGE.get(f['severity'], f['severity'])} · {f['title']}", "", f["rationale"]]
    if f.get("suggestion"):
        parts += ["", f"**Suggestion:** {f['suggestion']}"]
    if f.get("evidence"):
        items = "\n".join(f"- {e}" for e in f["evidence"])
        parts += ["", f"<details><summary>Evidence</summary>\n\n{items}\n\n</details>"]
    by = ", ".join([f["reviewer"], *f.get("merged_from", [])])
    parts += ["", f"<sub>CodeSage · {by} reviewer · confidence {f['confidence']:.0%}</sub>"]
    return "\n".join(parts)


def gate_outcome(state: dict, error: str | None = None) -> tuple[str, str]:
    """(check-run conclusion, title). Precedence: error > failure > neutral > success."""
    if error:
        return "failure", truncate(f"CodeSage error: {error}", 200)
    reasons = state.get("gate_reasons") or []
    blocking = [r for r in reasons if r != BUDGET_REASON]
    counts = Counter(f["severity"] for f in state.get("findings", []))
    score = state.get("judge_score")
    if blocking:
        severe = ", ".join(f"{counts[s]} {s}" for s in ("critical", "high") if counts[s])
        score_reason = next((r for r in blocking if r.startswith("judge score")), None)
        detail = severe or (score_reason or "").replace("judge score ", "score ")
        return "failure", f"Needs attention · {detail}"
    if reasons:
        return "neutral", "Budget reached before judging"
    return "success", "No blocking issues" + (f" · score {score:.2f}" if score is not None else "")


def _cost_table(telemetry: dict) -> str:
    by_agent: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0, 0])
    for ev in telemetry.get("by_component", []):
        row = by_agent[ev["component"]]
        row[0] += ev.get("input_tokens", 0) + ev.get("cache_read_tokens", 0) \
            + ev.get("cache_write_tokens", 0)
        row[1] += ev.get("output_tokens", 0)
        row[2] += ev.get("cost_usd", 0.0)
        row[3] += 1
    lines = ["| Agent | Calls | Input tokens | Output tokens | Cost |", "|---|---:|---:|---:|---:|"]
    for agent, (tin, tout, cost, calls) in by_agent.items():
        lines.append(f"| {agent} | {calls} | {int(tin):,} | {int(tout):,} | ${cost:.4f} |")
    lines.append(f"| **Total** | {telemetry.get('calls', 0)} | | | "
                 f"**${telemetry.get('total_cost_usd', 0.0):.4f}** |")
    return "\n".join(lines)


def review_body(
    state: dict,
    telemetry: dict,
    *,
    inline_ids: set[str] | None = None,
    skipped_files: list[str] | None = None,
    review_id: str | None = None,
) -> str:
    findings = state.get("findings", [])
    inline_ids = inline_ids or set()
    conclusion, title = gate_outcome(state)
    icon = {"success": "✅", "failure": "⚠️", "neutral": "➖"}[conclusion]
    score = state.get("judge_score")
    out = [f"## CodeSage review {icon} {title}", ""]
    if score is not None:
        out.append(f"**Judge score:** {score:.2f}"
                   + (f" (after {state['revision_count']} revision round)"
                      if state.get("revision_count") else ""))
    counts = Counter(f["severity"] for f in findings)
    if findings:
        out.append("**Findings:** " + " · ".join(
            f"{counts[s]} {s}" for s in SEVERITIES if counts[s]))
    else:
        out.append("**Findings:** none")
    if state.get("gate_reasons"):
        out += ["", "**Why this needs attention:**",
                *[f"- {r}" for r in state["gate_reasons"]]]
    if state.get("summary"):
        out += ["", "### Summary", "", state["summary"]]
    body_only = [f for f in findings if f.get("id") not in inline_ids]
    if body_only:
        out += ["", "### Findings" if not inline_ids else "### Findings not shown inline", ""]
        for f in body_only:
            by = ", ".join([f["reviewer"], *f.get("merged_from", [])])
            out.append(f"- {BADGE.get(f['severity'], f['severity'])} {_location(f)}"
                       f"**{f['title']}** ({by}): {f['rationale']}"
                       + (f" *Suggestion:* {f['suggestion']}" if f.get("suggestion") else ""))
    if skipped_files:
        out += ["", f"<sub>Not reviewed (over the file limit): {', '.join(skipped_files)}</sub>"]
    if state.get("budget_limited"):
        out += ["", "> ⚠️ The review budget was reached, so some agents stopped early"
                    + (" and the review wasn't judged." if score is None else ".")]
    out += ["", "<details><summary>Cost</summary>", "", _cost_table(telemetry), "", "</details>"]
    out += ["", f"<sub>CodeSage {codesage_version()} · agentic PR review with Claude</sub>"]
    if review_id:
        out.append(review_marker(review_id))
    return "\n".join(out)


def traces_markdown(state: dict) -> str:
    """Collapsed per-agent traces for the job summary."""
    traces = list(state.get("traces", []))
    if state.get("reflection_trace"):
        traces.append(state["reflection_trace"])
    out = ["", "### Agent traces", ""]
    for t in traces:
        steps = []
        for s in t["steps"]:
            tool = f" `{s['tool']}`" if s.get("tool") else ""
            content = s["content"].replace("\n", " ")[:400]
            steps.append(f"- **{s['kind']}**{tool}: {content}")
        out += [f"<details><summary>{t['agent']} ({len(t['steps'])} steps)</summary>", "",
                *steps, "", "</details>"]
    return "\n".join(out)
