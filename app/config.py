"""Central, environment-driven configuration.

Everything CodeSage needs to run is expressed as a setting here, so the GitHub
Action, the local API/console and the MCP server share one engine and only the
environment differs.
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
    # Reviewers and reflection use `model`; the judge is a different, stronger model
    # so it isn't grading its own work. `.codesage.yml` can override all of these.
    model: str = Field(default="claude-sonnet-5", alias="CODESAGE_MODEL")
    judge_model: str = Field(default="claude-opus-5", alias="CODESAGE_JUDGE_MODEL")
    reviewer_effort: str = Field(default="medium", alias="CODESAGE_REVIEWER_EFFORT")
    judge_effort: str = Field(default="high", alias="CODESAGE_JUDGE_EFFORT")
    max_tokens: int = Field(default=8000, alias="CODESAGE_MAX_TOKENS")
    # Hard cap on model spend per review (USD).
    budget_usd: float = Field(default=0.50, alias="CODESAGE_BUDGET_USD")

    # ── Embeddings ────────────────────────────────────────────────────────────
    voyage_api_key: str = Field(default="", alias="VOYAGE_API_KEY")
    embed_model: str = Field(default="voyage-3", alias="CODESAGE_EMBED_MODEL")
    embed_dim: int = Field(default=1024, alias="CODESAGE_EMBED_DIM")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://codesage:codesage@db:5432/codesage",
        alias="DATABASE_URL",
    )
    # LangGraph checkpointer (psycopg v3). Empty = derived from DATABASE_URL.
    checkpoint_dsn_override: str = Field(default="", alias="CODESAGE_CHECKPOINT_DSN")

    # ── GitHub ────────────────────────────────────────────────────────────────
    github_token: str = Field(default="", alias="GITHUB_TOKEN")

    # ── Behavior ──────────────────────────────────────────────────────────────
    hitl_threshold: float = Field(default=0.6, alias="CODESAGE_HITL_THRESHOLD")
    rag_top_k: int = Field(default=6, alias="CODESAGE_RAG_TOP_K")

    # ── Security ──────────────────────────────────────────────────────────────
    # Comma-separated API keys accepted on /api/v1 (Bearer or X-API-Key). With no
    # keys configured the API fails closed unless auth is explicitly disabled.
    api_keys: str = Field(default="", alias="CODESAGE_API_KEYS")
    # Local development only — never set this on a reachable deployment.
    auth_disabled: bool = Field(default=False, alias="CODESAGE_AUTH_DISABLED")
    # Comma-separated directories POST /ingest may read from. Empty disables
    # ingestion over the API (the CLI `scripts.ingest_repo` is unaffected).
    ingest_roots: str = Field(default="", alias="CODESAGE_INGEST_ROOTS")

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0", alias="CODESAGE_HOST")
    port: int = Field(default=8000, alias="CODESAGE_PORT")
    log_level: str = Field(default="INFO", alias="CODESAGE_LOG_LEVEL")
    # Comma-separated list of allowed CORS origins. Empty (the default) allows no
    # cross-origin callers; the web console is same-origin via its proxy.
    cors_origins: str = Field(default="", alias="CODESAGE_CORS_ORIGINS")

    @property
    def cors_origin_list(self) -> list[str]:
        return _split_csv(self.cors_origins)

    @property
    def api_key_list(self) -> list[str]:
        return _split_csv(self.api_keys)

    @property
    def ingest_root_list(self) -> list[str]:
        return _split_csv(self.ingest_roots)

    @property
    def has_llm(self) -> bool:
        """Whether a real Claude key is configured (otherwise we degrade to a stub)."""
        return bool(self.anthropic_api_key)

    @property
    def checkpoint_dsn(self) -> str:
        """psycopg DSN for the HITL checkpointer (the app itself uses asyncpg)."""
        return self.checkpoint_dsn_override or self.database_url.replace("+asyncpg", "", 1)

    @property
    def has_embeddings_api(self) -> bool:
        return bool(self.voyage_api_key)


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
