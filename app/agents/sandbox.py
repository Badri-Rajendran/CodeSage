"""Sandboxed test execution.

When a diff adds tests, CodeSage can run them in an isolated subprocess to gather
real signal for the correctness reviewer. The sandbox is intentionally
conservative: a temp working directory, a hard timeout, a restricted environment,
and no network expectation. It runs only code extracted from the diff's added
lines for files that look like tests.

SECURITY: this is process-level isolation only. The tests run as the API's own
user, with its network access and filesystem view, so they can read anything
the API process can (including its environment via /proc). It is therefore
disabled by default (``CODESAGE_SANDBOX_ENABLED=false``) and forced off in the
production compose file. Enable it only for trusted input; untrusted input
needs a disposable container/gVisor/microVM backend behind this interface.

On timeout the whole process group is killed (``app.workspace.proc``), so
tests that fork or sleep cannot outlive the review.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.logging_config import get_logger
from app.workspace.proc import run_bounded

logger = get_logger(__name__)

_FILE_HEADER = re.compile(r"^\+\+\+ b/(?P<path>.+)$")


@dataclass
class SandboxResult:
    ran: bool
    passed: bool
    summary: str
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None

    def as_dict(self) -> dict:
        return {
            "ran": self.ran,
            "passed": self.passed,
            "summary": self.summary,
            "returncode": self.returncode,
            "stdout_tail": self.stdout[-2000:],
            "stderr_tail": self.stderr[-2000:],
        }


def extract_added_files(diff: str) -> dict[str, str]:
    """Reconstruct added-line content per file from a unified diff."""
    files: dict[str, list[str]] = {}
    current: str | None = None
    for line in diff.splitlines():
        m = _FILE_HEADER.match(line)
        if m:
            current = m.group("path")
            files.setdefault(current, [])
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            files[current].append(line[1:])
    return {p: "\n".join(lines) for p, lines in files.items() if lines}


def _is_python_test(path: str) -> bool:
    base = os.path.basename(path)
    return path.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py"))


class Sandbox:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def run_tests(self, diff: str) -> SandboxResult:
        if not self.settings.sandbox_enabled:
            return SandboxResult(False, False, "Sandbox disabled by configuration.")

        added = extract_added_files(diff)
        test_files = {p: c for p, c in added.items() if _is_python_test(p)}
        if not test_files:
            return SandboxResult(False, False, "No Python test files in the diff to run.")

        with tempfile.TemporaryDirectory(prefix="codesage_sbx_") as tmp:
            for path, content in test_files.items():
                target = os.path.join(tmp, os.path.basename(path))
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(content)
            return await self._exec_pytest(tmp)

    async def _exec_pytest(self, workdir: str) -> SandboxResult:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": workdir,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        try:
            result = await run_bounded(
                [sys.executable, "-m", "pytest", "-q", workdir],
                cwd=workdir,
                env=env,
                timeout=self.settings.sandbox_timeout_s,
            )
        except FileNotFoundError:  # pragma: no cover
            return SandboxResult(False, False, "pytest not available in sandbox.")
        if result.timed_out:
            return SandboxResult(
                True, False, f"Tests exceeded {self.settings.sandbox_timeout_s}s timeout."
            )

        passed = result.returncode == 0
        summary = "All sandboxed tests passed." if passed else "Sandboxed tests failed."
        return SandboxResult(
            ran=True,
            passed=passed,
            summary=summary,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )
