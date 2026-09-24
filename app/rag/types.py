"""Plain data types shared by the RAG store and the review engine.

Kept free of SQLAlchemy/pgvector imports so the engine (the GitHub Action
install) can use them without the server extra.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    repo: str
    path: str
    start_line: int
    end_line: int
    content: str
    score: float

    def cite(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"
