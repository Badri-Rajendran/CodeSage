"""Embeddings provider.

Uses Voyage AI embeddings when VOYAGE_API_KEY is set (Voyage's `voyage-code`/
`voyage-3` models are well-suited to code retrieval). Otherwise falls back to a
deterministic local hashing embedder so the RAG pipeline works offline — it's a
bag-of-token-hashes projected into the configured dimension, L2-normalized. Not
semantically rich, but stable and dependency-free for dev/demo.
"""

from __future__ import annotations

import hashlib
import math
import re

import httpx

from app.config import Settings, get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[^\sA-Za-z0-9_]")


class Embedder:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.dim = self.settings.embed_dim
        self._use_api = self.settings.has_embeddings_api

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._use_api:
            try:
                return await self._embed_voyage(texts)
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("Voyage embedding failed (%s); using local fallback.", exc)
        return [self._embed_local(t) for t in texts]

    async def _embed_voyage(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.settings.voyage_api_key}"},
                json={"input": texts, "model": self.settings.embed_model},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]

    def _embed_local(self, text: str) -> list[float]:
        """Deterministic hashing embedding into `self.dim` dims, L2-normalized."""
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall(text.lower()):
            h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec
