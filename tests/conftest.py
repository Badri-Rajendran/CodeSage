"""Test configuration.

Forces stub LLM mode (no API key) and a dummy DB DSN so the suite runs fully
offline. Env is set *before* any app import so cached Settings pick it up.
"""

from __future__ import annotations

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ["ANTHROPIC_API_KEY"] = ""  # ensure stub mode even if a key is exported
os.environ.setdefault("VOYAGE_API_KEY", "")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://codesage:codesage@localhost:5432/codesage_test"
)
os.environ.setdefault("CODESAGE_SANDBOX_ENABLED", "true")
os.environ["CODESAGE_API_KEYS"] = "test-key"
os.environ["CODESAGE_AUTH_DISABLED"] = "false"

API_KEY = "test-key"
