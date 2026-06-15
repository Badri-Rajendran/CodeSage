"""Central, environment-driven configuration.

Everything CodeSage needs to run is expressed as a setting here so the same
image runs unchanged across local Docker, a DigitalOcean droplet, or AWS
(ECS/Lambda) — only the environment differs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Claude API ────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    model: str = Field(default="claude-opus-4-8", alias="CODESAGE_MODEL")
    judge_model: str = Field(default="claude-opus-4-8", alias="CODESAGE_JUDGE_MODEL")
    effort: str = Field(default="high", alias="CODESAGE_EFFORT")
    max_tokens: int = Field(default=8000, alias="CODESAGE_MAX_TOKENS")

    # ── Embeddings ────────────────────────────────────────────────────────────
    voyage_api_key: str = Field(default="", alias="VOYAGE_API_KEY")
    embed_model: str = Field(default="voyage-3", alias="CODESAGE_EMBED_MODEL")
    embed_dim: int = Field(default=1024, alias="CODESAGE_EMBED_DIM")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://codesage:codesage@db:5432/codesage",
        alias="DATABASE_URL",
    )

    # ── GitHub ────────────────────────────────────────────────────────────────
    github_token: str = Field(default="", alias="GITHUB_TOKEN")

    # ── Behavior ──────────────────────────────────────────────────────────────
    hitl_threshold: float = Field(default=0.6, alias="CODESAGE_HITL_THRESHOLD")
    rag_top_k: int = Field(default=6, alias="CODESAGE_RAG_TOP_K")
    sandbox_enabled: bool = Field(default=True, alias="CODESAGE_SANDBOX_ENABLED")
    sandbox_timeout_s: int = Field(default=30, alias="CODESAGE_SANDBOX_TIMEOUT_S")

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0", alias="CODESAGE_HOST")
    port: int = Field(default=8000, alias="CODESAGE_PORT")
    log_level: str = Field(default="INFO", alias="CODESAGE_LOG_LEVEL")

    @property
    def has_llm(self) -> bool:
        """Whether a real Claude key is configured (otherwise we degrade to a stub)."""
        return bool(self.anthropic_api_key)

    @property
    def has_embeddings_api(self) -> bool:
        return bool(self.voyage_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
