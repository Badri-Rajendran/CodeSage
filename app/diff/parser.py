"""Unified-diff parser with RIGHT-side line ranges.

GitHub review comments address a line with ``line`` + ``side: RIGHT``: the line
number in the PR head version of the file. Only lines inside a hunk (added or
context lines) can be commented on. Each hunk's new-side lines are contiguous,
so a file's commentable lines are the union of ``[new_start, new_start +
new_len - 1]`` over its hunks.

Hunk bodies are consumed by line count, so a removed line such as ``--- x`` is
never mistaken for a file header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

FileStatus = Literal["added", "modified", "deleted", "renamed"]

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_GIT_HEADER_RE = re.compile(r"^diff --git (\"?a/.+?\"?) (\"?b/.+\"?)$")


def _strip_prefix(raw: str) -> str | None:
    """``a/x`` / ``b/x`` / ``"b/x y"`` / ``/dev/null`` -> repo-relative path or None."""
    raw = raw.strip().split("\t", 1)[0]
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    if raw == "/dev/null":
        return None
    if raw[:2] in ("a/", "b/"):
        return raw[2:]
    return raw


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str
    lines: list[str] = field(default_factory=list)

    @property
    def right_range(self) -> tuple[int, int] | None:
        if self.new_len == 0:
            return None
        return (self.new_start, self.new_start + self.new_len - 1)


@dataclass
class FileDiff:
    path: str
    old_path: str | None
    status: FileStatus
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False
    raw: str = ""

    @property
    def right_ranges(self) -> list[tuple[int, int]]:
        return [r for h in self.hunks if (r := h.right_range) is not None]

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.startswith("+"))

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.startswith("-"))

    def is_commentable(self, line: int) -> bool:
        return any(lo <= line <= hi for lo, hi in self.right_ranges)


@dataclass
class Diff:
    files: list[FileDiff] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    def for_path(self, path: str) -> FileDiff | None:
        return next((f for f in self.files if f.path == path), None)

    def is_commentable(self, path: str | None, line: int | None) -> bool:
        if not path or line is None:
            return False
        f = self.for_path(path)
        return f is not None and f.is_commentable(line)

    def filter(self, ignore_globs: list[str]) -> Diff:
        """Drop files whose path matches any glob (``**`` spans directories)."""
        pats = [glob_to_regex(g) for g in ignore_globs]
        return Diff([f for f in self.files if not any(p.match(f.path) for p in pats)])

    def limit(self, max_files: int) -> tuple[Diff, list[FileDiff]]:
        """Keep the first ``max_files`` files; return (kept, skipped)."""
        return Diff(self.files[:max_files]), self.files[max_files:]

    def render(self, path: str | None = None) -> str:
        if path is None:
            return "".join(f.raw for f in self.files)
        f = self.for_path(path)
        return f.raw if f else ""

    def summary(self) -> str:
        """One line per file: status, +adds/-dels, path."""
        return "\n".join(
            f"{f.status:<8} +{f.additions:<4} -{f.deletions:<4} {f.path}"
            + (" (binary)" if f.binary else "")
            + (f" (from {f.old_path})" if f.status == "renamed" else "")
            for f in self.files
        )


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate a path glob: ``**/`` = any dirs (or none), ``*`` = within a segment."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


class _Builder:
    """Accumulates one file's header lines, hunks, and raw text."""

    def __init__(self) -> None:
        self.old: str | None = None
        self.new: str | None = None
        self.git_old: str | None = None
        self.git_new: str | None = None
        self.new_file = self.deleted_file = self.renamed = self.binary = False
        self.hunks: list[Hunk] = []
        self.raw: list[str] = []

    def build(self) -> FileDiff | None:
        new = self.new if self.new is not None else self.git_new
        old = self.old if self.old is not None else self.git_old
        if self.deleted_file or (self.new is None and self.old is not None and not self.git_new):
            status: FileStatus = "deleted"
        elif self.new_file or (self.old is None and self.new is not None and not self.git_old):
            status = "added"
        elif self.renamed or (old and new and old != new):
            status = "renamed"
        else:
            status = "modified"
        path = new if status != "deleted" else old
        if not path:
            return None
        return FileDiff(
            path=path,
            old_path=old if status == "renamed" else None,
            status=status,
            hunks=self.hunks,
            binary=self.binary,
            raw="".join(self.raw),
        )


def parse_diff(text: str) -> Diff:
    """Parse a unified diff (``git diff`` or GitHub's ``.diff``) into a ``Diff``."""
    files: list[FileDiff] = []
    cur: _Builder | None = None
    hunk: Hunk | None = None
    old_left = new_left = 0

    def flush() -> None:
        nonlocal cur
        if cur is not None and (fd := cur.build()) is not None:
            files.append(fd)
        cur = None

    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip("\r\n")
        i += 1

        # Inside a hunk: consume body lines by count.
        if hunk is not None and (old_left > 0 or new_left > 0):
            tag = line[:1]
            if tag in (" ", "-", "+", "\\") or line == "":
                assert cur is not None
                cur.raw.append(raw)
                if tag == "\\":  # "\ No newline at end of file"
                    continue
                hunk.lines.append(line if line else " ")
                if tag == "-":
                    old_left -= 1
                elif tag == "+":
                    new_left -= 1
                else:
                    old_left -= 1
                    new_left -= 1
                continue
            hunk = None  # malformed/short hunk: fall through to header parsing
        elif hunk is not None and line.startswith("\\"):
            assert cur is not None
            cur.raw.append(raw)
            continue

        if m := _GIT_HEADER_RE.match(line):
            flush()
            cur = _Builder()
            cur.git_old, cur.git_new = _strip_prefix(m.group(1)), _strip_prefix(m.group(2))
            cur.raw.append(raw)
            hunk = None
            continue

        if line.startswith("--- ") and i < len(lines) and lines[i].startswith("+++ "):
            if cur is None or cur.hunks or cur.old is not None:
                flush()
                cur = _Builder()
            cur.old = _strip_prefix(line[4:])
            cur.new = _strip_prefix(lines[i].rstrip("\r\n")[4:])
            cur.raw.extend([raw, lines[i]])
            i += 1
            hunk = None
            continue

        if cur is None:
            continue  # preamble (e.g. commit message) before the first file

        cur.raw.append(raw)
        if m := _HUNK_RE.match(line):
            hunk = Hunk(
                old_start=int(m.group(1)),
                old_len=int(m.group(2)) if m.group(2) is not None else 1,
                new_start=int(m.group(3)),
                new_len=int(m.group(4)) if m.group(4) is not None else 1,
                header=m.group(5).strip(),
            )
            old_left, new_left = hunk.old_len, hunk.new_len
            cur.hunks.append(hunk)
        elif line.startswith("new file mode"):
            cur.new_file = True
        elif line.startswith("deleted file mode"):
            cur.deleted_file = True
        elif line.startswith("rename from "):
            cur.renamed, cur.old = True, line[len("rename from ") :]
        elif line.startswith("rename to "):
            cur.renamed, cur.new = True, line[len("rename to ") :]
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            cur.binary = True

    flush()
    return Diff(files)
