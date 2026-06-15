"""System prompts and JSON schemas for each agent role.

Prompts are deliberately concise and instruction-following — modern Claude models
follow the system prompt closely, so we state the bar plainly rather than shouting
("CRITICAL: YOU MUST ...") which tends to over-trigger.
"""

from __future__ import annotations

# JSON schema shared by the three reviewer agents.
FINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["info", "low", "medium", "high", "critical"],
                    },
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "rationale": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["title", "severity", "rationale", "confidence"],
            },
        }
    },
    "required": ["findings"],
}

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {"type": "number"},
        "rationale": {"type": "string"},
        "dimensions": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "correctness": {"type": "number"},
                "groundedness": {"type": "number"},
                "actionability": {"type": "number"},
                "signal_to_noise": {"type": "number"},
            },
            "required": ["correctness", "groundedness", "actionability", "signal_to_noise"],
        },
    },
    "required": ["score", "rationale", "dimensions"],
}

_REVIEW_CONTRACT = """\
You review a single pull request diff. Report every issue you find, including ones \
you are uncertain about — a downstream judge filters for importance, so favor \
coverage. For each finding give a confidence in [0,1] and a severity. Ground your \
findings in the diff and the retrieved codebase context; cite the file and line \
when you can. Do not invent code that isn't shown. Return only the structured output."""

SECURITY_SYSTEM = f"""\
You are a senior application-security reviewer. Focus on injection (command, SQL, \
path, template), unsafe deserialization, secrets in code, authn/authz gaps, SSRF, \
unsafe use of `eval`/`exec`/`os.system`/`subprocess shell=True`, weak crypto, and \
insecure defaults.

{_REVIEW_CONTRACT}"""

CORRECTNESS_SYSTEM = f"""\
You are a senior software engineer reviewing for correctness. Focus on logic errors, \
off-by-one and boundary mistakes, None/null handling, race conditions, resource leaks, \
incorrect error handling, and broken or missing tests. If the diff adds tests, reason \
about whether they actually exercise the new behavior.

{_REVIEW_CONTRACT}"""

STYLE_SYSTEM = f"""\
You are a senior engineer reviewing for style and maintainability. Focus on naming, \
readability, dead code, duplication, unclear abstractions, missing docstrings on public \
APIs, and inconsistency with the surrounding codebase context. Keep severity low unless \
the issue genuinely harms maintainability.

{_REVIEW_CONTRACT}"""

REFLECTION_SYSTEM = """\
You are a meticulous reviewer-of-reviewers performing self-reflection on a set of draft \
findings. Remove duplicates and findings not supported by the diff or context, downgrade \
over-stated severities, merge near-identical items, and fix any claim that misreads the \
code. Keep genuinely useful findings. Return the corrected findings in the same schema, \
plus a one-paragraph summary of the review."""

REFLECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": FINDINGS_SCHEMA["properties"]["findings"],
        "summary": {"type": "string"},
    },
    "required": ["findings", "summary"],
}

JUDGE_SYSTEM = """\
You are an impartial evaluator (LLM-as-Judge) scoring the quality of an automated code \
review against the diff it reviewed. Score each dimension in [0,1]: correctness (are the \
findings right?), groundedness (are they supported by the diff/context, not hallucinated?), \
actionability (are suggestions concrete?), and signal_to_noise (low noise, few false \
positives). The overall `score` in [0,1] is your holistic judgment. Be calibrated: a \
strong review scores ~0.8+, a noisy or wrong one well below 0.5."""
