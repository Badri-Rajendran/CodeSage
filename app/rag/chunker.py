"""Source-file chunking for the RAG pipeline.

A pragmatic, language-agnostic line-window chunker with overlap. Good enough to
give reviewers grounded surrounding context; swap in a tree-sitter chunker later
for symbol-aware splitting without changing the ingestion interface.
"""

from __future__ import annotations

from dataclasses import dataclass

_LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".sql": "sql",
    ".sh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
}


@dataclass
class Chunk:
    repo: str
    path: str
    start_line: int
    end_line: int
    language: str | None
    content: str

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "language": self.language,
            "content": self.content,
        }


def language_for(path: str) -> str | None:
    for ext, lang in _LANG_BY_EXT.items():
        if path.endswith(ext):
            return lang
    return None


def chunk_file(
    repo: str,
    path: str,
    text: str,
    *,
    window: int = 60,
    overlap: int = 12,
) -> list[Chunk]:
    """Split a file into overlapping line windows."""
    lines = text.splitlines()
    if not lines:
        return []
    step = max(1, window - overlap)
    chunks: list[Chunk] = []
    lang = language_for(path)
    for start in range(0, len(lines), step):
        block = lines[start : start + window]
        if not any(ln.strip() for ln in block):
            continue
        chunks.append(
            Chunk(
                repo=repo,
                path=path,
                start_line=start + 1,
                end_line=start + len(block),
                language=lang,
                content="\n".join(block),
            )
        )
        if start + window >= len(lines):
            break
    return chunks
