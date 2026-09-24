"""API-key authentication for the REST API.

Clients send a key as ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.
Keys come from ``CODESAGE_API_KEYS``. The API fails closed: with no keys
configured every protected route returns 503 unless ``CODESAGE_AUTH_DISABLED``
is set (local development only).
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    return None


def _matches(candidate: str, keys: list[str]) -> bool:
    # Compare against every key so timing doesn't reveal which (if any) matched.
    ok = False
    for key in keys:
        ok |= hmac.compare_digest(candidate.encode(), key.encode())
    return ok


async def require_api_key(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
) -> None:
    settings = get_settings()
    if settings.auth_disabled:
        return
    keys = settings.api_key_list
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured: set CODESAGE_API_KEYS.",
        )
    candidate = _extract_key(authorization, x_api_key)
    if candidate is None or not _matches(candidate, keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
