"""A repository at a commit, and the agent tools that read it.

The reviewer agents investigate a PR through these tools: read files, search
code, list directories, look at history and blame, view the diff, and (for the
correctness reviewer in the GitHub Action) run the repo's own tests.

Rules every tool follows:
  - paths are resolved (symlinks included) and must stay inside the root;
    anything under ``.git/`` is refused
  - results are capped (``MAX_TOOL_OUTPUT`` chars) and say when they were cut
  - failures come back as ``"error: ..."`` strings, never exceptions, so the
    agent can recover and narrow its request
  - ``run_tests`` gets a scrubbed environment: no API keys or tokens

Three shapes:
  - ``Workspace(root, diff)``: an existing checkout (the Action's runner)
  - ``Workspace.clone_at(...)``: a temporary blobless clone (local PR mode)
  - ``Workspace.diff_only(diff)``: no checkout at all (local diff mode)
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from app.diff import Diff
from app.github.client import validate_repo
from app.logging_config import get_logger
from app.rag.types import RetrievedChunk
from app.workspace.proc import run_bounded

logger = get_logger(__name__)

MAX_TOOL_OUTPUT = 12_000
READ_MAX_LINES = 400
READ_MAX_BYTES = 40_000
SEARCH_MAX_MATCHES = 50
LIST_MAX_ENTRIES = 200
BLAME_MAX_LINES = 200
TEST_OUTPUT_TAIL = 4_000
GIT_TIMEOUT_S = 30
CLONE_TIMEOUT_S = 300

SemanticSearch = Callable[[str, int], Awaitable[list[RetrievedChunk]]]

# Environment variables never passed to run_tests (repo code we don't control).
_SECRET_NAME = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
_SECRET_PREFIXES = ("ACTIONS_", "CODESAGE_", "ANTHROPIC_", "GITHUB_TOKEN", "VOYAGE_")


@dataclass(frozen=True)
class TestSpec:
    """How to run the repo's tests (from ``.codesage.yml`` ``tests:``)."""

    command: str
    setup: str | None = None
    timeout_s: int = 300


def scrubbed_env() -> dict[str, str]:
    """The current environment minus anything that looks like a credential."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_SECRET_PREFIXES) and not _SECRET_NAME.search(k)
    }


def _cap(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated: {len(text) - limit} more chars; narrow the request]"


def _git_auth_env(token: str | None) -> dict[str, str]:
    """Pass the token as an HTTP header via env config: never written to .git/config."""
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


class Workspace:
    def __init__(
        self,
        root: Path | None,
        diff: Diff,
        *,
        tests: TestSpec | None = None,
        semantic: SemanticSearch | None = None,
        git_env: dict[str, str] | None = None,
    ):
        self.root = root.resolve() if root is not None else None
        self.diff = diff
        self.tests = tests
        self.semantic = semantic
        self._git_env = git_env or {}
        self._setup_done = False
        self._setup_error: str | None = None
        self._setup_lock = asyncio.Lock()

    # ── constructors ────────────────────────────────────────────────────────

    @classmethod
    def diff_only(cls, diff: Diff, *, semantic: SemanticSearch | None = None) -> Workspace:
        return cls(None, diff, semantic=semantic)

    @classmethod
    @asynccontextmanager
    async def clone_at(
        cls,
        repo: str,
        head_sha: str,
        diff: Diff,
        *,
        base_sha: str | None = None,
        token: str | None = None,
        semantic: SemanticSearch | None = None,
    ) -> AsyncIterator[Workspace]:
        """Blobless clone of ``repo`` checked out at ``head_sha``; deleted on exit."""
        validate_repo(repo)
        if not re.fullmatch(r"[0-9a-f]{7,40}", head_sha) or (
            base_sha and not re.fullmatch(r"[0-9a-f]{7,40}", base_sha)
        ):
            raise ValueError("head_sha/base_sha must be hex commit ids")
        tmp = Path(tempfile.mkdtemp(prefix="codesage_ws_"))
        auth = _git_auth_env(token)
        try:
            url = f"https://github.com/{repo}.git"
            steps = [
                ["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", url, str(tmp)],
                ["git", "-C", str(tmp), "fetch", "--quiet", "--filter=blob:none", "origin",
                 head_sha, *([base_sha] if base_sha else [])],
                ["git", "-C", str(tmp), "checkout", "--quiet", "--detach", head_sha],
            ]
            for argv in steps:
                res = await run_bounded(
                    argv, cwd=tmp.parent, env={**_base_git_env(), **auth},
                    timeout=CLONE_TIMEOUT_S,
                )
                if res.timed_out or res.returncode != 0:
                    raise RuntimeError(
                        f"git {argv[1] if argv[1] != '-C' else argv[3]} failed: "
                        f"{'timeout' if res.timed_out else res.stderr.strip()[-500:]}"
                    )
            yield cls(tmp, diff, semantic=semantic, git_env=auth)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @property
    def has_repo(self) -> bool:
        return self.root is not None

    # ── helpers ─────────────────────────────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        """Resolve a repo-relative path, refusing escapes and ``.git``."""
        assert self.root is not None
        rel = path.strip().lstrip("/") or "."
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root):
            raise PermissionError(f"path escapes the repository: {path}")
        # Case-folded: on case-insensitive filesystems ".GIT/config" is .git/config.
        if any(p.casefold() == ".git" for p in target.relative_to(self.root).parts):
            raise PermissionError("the .git directory is off limits")
        return target

    async def _git(self, *args: str) -> str:
        assert self.root is not None
        res = await run_bounded(
            ["git", *args], cwd=self.root, env={**_base_git_env(), **self._git_env},
            timeout=GIT_TIMEOUT_S,
        )
        if res.timed_out:
            raise TimeoutError(f"git {args[0]} timed out")
        if res.returncode not in (0, 1):  # 1 = "no matches" for grep
            raise RuntimeError(res.stderr.strip()[-500:] or f"git {args[0]} failed")
        return res.stdout

    # ── tools ───────────────────────────────────────────────────────────────

    async def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Read numbered lines of a repository file (max 400 lines per call)."""
        target = self._resolve(path)
        if not target.is_file():
            return f"error: not a file: {path}"
        data = target.read_bytes()
        if b"\0" in data[:8192]:
            return f"error: binary file: {path}"
        lines = data.decode(errors="replace").splitlines()
        start = max(1, start_line)
        end = min(len(lines), end_line or start + READ_MAX_LINES - 1, start + READ_MAX_LINES - 1)
        if start > len(lines):
            return f"error: {path} has only {len(lines)} lines"
        out, size = [], 0
        for n in range(start, end + 1):
            row = f"{n}: {lines[n - 1]}"
            size += len(row) + 1
            if size > READ_MAX_BYTES:
                out.append(f"... [stopped at line {n - 1}: 40 KB limit]")
                break
            out.append(row)
        more = f"\n... [{len(lines) - end} more lines]" if end < len(lines) else ""
        return f"{path} (lines {start}-{end} of {len(lines)})\n" + "\n".join(out) + more

    async def search_code(
        self, pattern: str, path_glob: str | None = None, fixed: bool = False,
        ignore_case: bool = False,
    ) -> str:
        """Search tracked files with git grep (regex, or a literal when fixed=True)."""
        args = ["grep", "-n", "-I", "-F" if fixed else "-E"]
        if ignore_case:
            args.append("-i")
        args += ["-e", pattern, "--"]
        if path_glob:
            args.append(f":(glob){path_glob}")
        lines = (await self._git(*args)).splitlines()
        if not lines:
            return "no matches"
        shown = lines[:SEARCH_MAX_MATCHES]
        extra = len(lines) - len(shown)
        return "\n".join(shown) + (f"\n... [{extra} more matches]" if extra else "")

    async def list_dir(self, path: str = ".") -> str:
        """List tracked files and subdirectories directly under a directory."""
        target = self._resolve(path)
        assert self.root is not None
        rel = target.relative_to(self.root).as_posix()
        prefix = "" if rel == "." else rel + "/"
        files = (await self._git("ls-files", "--", prefix or ".")).splitlines()
        entries: set[str] = set()
        for f in files:
            rest = f[len(prefix):]
            head, sep, _ = rest.partition("/")
            entries.add(head + ("/" if sep else ""))
        if not entries:
            return f"error: no tracked files under {path}"
        ordered = sorted(entries, key=lambda e: (not e.endswith("/"), e))
        shown = ordered[:LIST_MAX_ENTRIES]
        extra = len(ordered) - len(shown)
        return "\n".join(shown) + (f"\n... [{extra} more entries]" if extra else "")

    async def git_log(self, path: str, max_count: int = 10) -> str:
        """Recent commits touching a path (one line each)."""
        self._resolve(path)
        out = await self._git("log", "--oneline", f"-n{max(1, min(max_count, 50))}", "--", path)
        return out.strip() or "no history"

    async def git_blame(self, path: str, start_line: int, end_line: int) -> str:
        """Who last changed each line in a range (max 200 lines)."""
        self._resolve(path)
        end_line = min(end_line, start_line + BLAME_MAX_LINES - 1)
        return (await self._git(
            "blame", "--date=short", "-L", f"{start_line},{end_line}", "--", path
        )).strip()

    async def get_diff(self, path: str | None = None) -> str:
        """The PR diff for one file, or (no path) the changed-file list with counts."""
        if path is None:
            return self.diff.summary() or "empty diff"
        text = self.diff.render(path)
        return text or f"error: {path} is not in the diff"

    async def run_tests(self, target: str | None = None) -> str:
        """Run the repo's configured test command, optionally for one target."""
        assert self.tests is not None and self.root is not None
        if target is not None and (target.startswith("-") or "\n" in target):
            return "error: target must be a test path or id, not an option"
        async with self._setup_lock:
            if not self._setup_done and self.tests.setup:
                res = await run_bounded(
                    self.tests.setup, cwd=self.root, env=scrubbed_env(),
                    timeout=self.tests.timeout_s, merge_stderr=True,
                )
                if res.timed_out:
                    self._setup_error = f"timed out after {self.tests.timeout_s}s"
                elif res.returncode != 0:
                    tail = res.stdout[-TEST_OUTPUT_TAIL:].strip()
                    detail = f"\n{tail}" if tail else ""
                    self._setup_error = f"exit code {res.returncode}{detail}"
            self._setup_done = True
        if self._setup_error:
            return f"error: test setup failed ({self.tests.setup}):\n{self._setup_error}"
        cmd = self.tests.command
        cmd = cmd.replace("{target}", shlex.quote(target) if target else "")
        res = await run_bounded(
            cmd, cwd=self.root, env=scrubbed_env(), timeout=self.tests.timeout_s,
            merge_stderr=True,
        )
        if res.timed_out:
            return f"error: tests exceeded {self.tests.timeout_s}s and were killed"
        return f"exit code {res.returncode}\n{res.stdout[-TEST_OUTPUT_TAIL:]}"

    async def semantic_search(self, query: str, top_k: int = 6) -> str:
        """Semantic search over the ingested codebase index (local mode)."""
        assert self.semantic is not None
        chunks = await self.semantic(query, max(1, min(top_k, 12)))
        if not chunks:
            return "no results (repository may not be ingested)"
        return "\n\n".join(
            f"### {c.cite()} (similarity {c.score:.2f})\n{c.content}" for c in chunks
        )

    # ── LangChain tool objects ──────────────────────────────────────────────

    def tools_for(self, role: str, *, read_only: bool = False) -> list[BaseTool]:
        """Tools offered to an agent. ``read_only`` is the reflection subset."""
        names: list[str] = []
        if self.has_repo:
            names = ["read_file", "search_code", "get_diff"]
            if not read_only:
                names += ["list_dir", "git_log", "git_blame"]
                if role == "correctness" and self.tests and self.tests.command:
                    names.append("run_tests")
        else:
            names = ["get_diff"]
        if self.semantic is not None:
            names.append("semantic_search")
        return [self._tool(n) for n in names]

    def _tool(self, name: str) -> BaseTool:
        fn = getattr(self, name)

        async def call(**kwargs: object) -> str:
            try:
                return _cap(await fn(**kwargs))
            except Exception as exc:  # tool errors go back to the model, not up
                return f"error: {exc}"

        tool = StructuredTool.from_function(
            coroutine=fn, name=name, description=(fn.__doc__ or name).strip()
        )
        tool.coroutine = call
        return tool


def _base_git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
