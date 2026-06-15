"""Offline stub responses.

When no ANTHROPIC_API_KEY is configured, CodeSage degrades to a deterministic
heuristic engine so the API, demo, and test suite still work end-to-end without
network access. The stub mirrors the structured-output contract each component
expects. It is NOT a replacement for the model — just enough signal to exercise
the full pipeline offline.
"""

from __future__ import annotations

import re

# (regex, severity, title, rationale, suggestion)
_SECURITY_PATTERNS: list[tuple[str, str, str, str, str]] = [
    (
        r"os\.system\s*\(",
        "critical",
        "Command injection via os.system",
        "os.system passes its argument to a shell; with untrusted input this allows "
        "arbitrary command execution.",
        "Use subprocess.run([...], shell=False) with an argument list.",
    ),
    (
        r"subprocess\.\w+\([^)]*shell\s*=\s*True",
        "high",
        "Shell injection via subprocess(shell=True)",
        "shell=True invokes a shell, exposing the call to injection if any argument is "
        "attacker-influenced.",
        "Pass an argument list and drop shell=True.",
    ),
    (
        r"\beval\s*\(",
        "high",
        "Use of eval()",
        "eval executes arbitrary Python; dangerous with any untrusted input.",
        "Replace with ast.literal_eval or explicit parsing.",
    ),
    (
        r"\bexec\s*\(",
        "high",
        "Use of exec()",
        "exec executes arbitrary code and is rarely necessary.",
        "Refactor to avoid dynamic code execution.",
    ),
    (
        r"(?i)(password|secret|api[_-]?key|token)\s*=\s*['\"][^'\"]+['\"]",
        "high",
        "Hardcoded secret",
        "A credential appears to be hardcoded in source.",
        "Load secrets from environment variables or a secrets manager.",
    ),
    (
        r"verify\s*=\s*False",
        "medium",
        "TLS verification disabled",
        "Disabling certificate verification exposes the connection to MITM attacks.",
        "Remove verify=False and fix the underlying certificate trust issue.",
    ),
    (
        r"yaml\.load\s*\((?![^)]*Loader)",
        "high",
        "Unsafe yaml.load",
        "yaml.load without a safe Loader can construct arbitrary Python objects.",
        "Use yaml.safe_load.",
    ),
]

_CORRECTNESS_PATTERNS: list[tuple[str, str, str, str, str]] = [
    (
        r"except\s*:\s*$",
        "medium",
        "Bare except clause",
        "A bare except swallows all exceptions including KeyboardInterrupt/SystemExit "
        "and hides real errors.",
        "Catch specific exception types.",
    ),
    (
        r"==\s*None\b|!=\s*None\b",
        "low",
        "Comparison to None with ==/!=",
        "Identity should be tested with `is`/`is not`, not equality.",
        "Use `is None` / `is not None`.",
    ),
    (
        r"open\s*\([^)]*\)(?!\s*as)",
        "low",
        "File opened without context manager",
        "Opening a file without `with` risks leaking the file descriptor on error.",
        "Use `with open(...) as f:`.",
    ),
]

_STYLE_PATTERNS: list[tuple[str, str, str, str, str]] = [
    (
        r"#\s*(TODO|FIXME|XXX)\b",
        "info",
        "Leftover TODO/FIXME",
        "A TODO/FIXME marker is being introduced.",
        "Resolve before merge or link a tracking issue.",
    ),
    (
        r"\bprint\s*\(",
        "low",
        "print() used instead of logging",
        "print statements are hard to control in production.",
        "Use the logging module.",
    ),
]


def _scan(diff: str, patterns: list[tuple[str, str, str, str, str]]) -> list[dict]:
    findings: list[dict] = []
    # Only consider added lines (those starting with '+', not the +++ header).
    added = [
        ln[1:]
        for ln in diff.splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    blob = "\n".join(added)
    for rx, severity, title, rationale, suggestion in patterns:
        if re.search(rx, blob, flags=re.MULTILINE):
            findings.append(
                {
                    "title": title,
                    "severity": severity,
                    "rationale": rationale,
                    "suggestion": suggestion,
                    "confidence": 0.7,
                }
            )
    return findings


def generate(component: str, payload: dict) -> dict:
    """Produce a deterministic structured response for a component."""
    diff = payload.get("diff", "")

    if component == "security":
        return {"findings": _scan(diff, _SECURITY_PATTERNS)}
    if component == "correctness":
        return {"findings": _scan(diff, _CORRECTNESS_PATTERNS)}
    if component == "style":
        return {"findings": _scan(diff, _STYLE_PATTERNS)}

    if component == "reflection":
        findings = payload.get("findings", [])
        # De-dup by title, keep highest confidence.
        seen: dict[str, dict] = {}
        for f in findings:
            key = f.get("title", "")
            if key not in seen or f.get("confidence", 0) > seen[key].get("confidence", 0):
                seen[key] = f
        kept = list(seen.values())
        summary = (
            f"Reviewed the diff and consolidated {len(findings)} draft findings into "
            f"{len(kept)} after removing duplicates."
            if findings
            else "No issues found in the diff; the change looks clean."
        )
        return {"findings": kept, "summary": summary}

    if component == "judge":
        findings = payload.get("findings", [])
        # Heuristic: grounded reviews with a few high-confidence findings score well;
        # empty reviews on a non-trivial diff score moderately.
        n = len(findings)
        if n == 0:
            score = 0.65
        else:
            avg_conf = sum(f.get("confidence", 0.5) for f in findings) / n
            score = min(0.95, 0.55 + 0.4 * avg_conf)
        return {
            "score": round(score, 3),
            "rationale": "Heuristic offline judge: scored on finding count and confidence.",
            "dimensions": {
                "correctness": round(score, 3),
                "groundedness": round(score, 3),
                "actionability": round(min(0.9, score + 0.05), 3),
                "signal_to_noise": round(score, 3),
            },
        }

    return {"findings": []}
