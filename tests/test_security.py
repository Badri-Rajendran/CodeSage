"""Tests for the API lockdown: auth, ingest roots, dataset paths, repo validation,
sandbox defaults/timeouts, and stderr-only logging."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app.agents.sandbox import Sandbox
from app.config import Settings, get_settings
from app.eval.store import resolve_dataset
from app.github.client import GitHubClient, validate_repo
from app.logging_config import configure_logging
from app.main import app
from app.rag import pipeline
from app.rag.pipeline import IngestPathError, resolve_ingest_root
from tests.conftest import API_KEY

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {API_KEY}"}
# A request that passes auth but fails body validation, so no DB is needed.
INVALID_REVIEW = {"repo": "demo/x"}


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **update: object) -> None:
    patched = get_settings().model_copy(update=update)
    monkeypatch.setattr("app.api.auth.get_settings", lambda: patched)


# ── Authentication ─────────────────────────────────────────────────────────────
def test_missing_key_is_rejected():
    resp = client.post("/api/v1/reviews", json=INVALID_REVIEW)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_wrong_key_is_rejected():
    resp = client.get("/api/v1/reviews", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_non_bearer_scheme_is_rejected():
    resp = client.get("/api/v1/reviews", headers={"Authorization": f"Basic {API_KEY}"})
    assert resp.status_code == 401


def test_bearer_key_is_accepted():
    resp = client.post("/api/v1/reviews", json=INVALID_REVIEW, headers=AUTH)
    assert resp.status_code == 422  # past auth, into body validation


def test_x_api_key_header_is_accepted():
    resp = client.post("/api/v1/reviews", json=INVALID_REVIEW, headers={"X-API-Key": API_KEY})
    assert resp.status_code == 422


def test_any_configured_key_is_accepted(monkeypatch):
    _patch_settings(monkeypatch, api_keys="first, second")
    resp = client.post(
        "/api/v1/reviews", json=INVALID_REVIEW, headers={"Authorization": "Bearer second"}
    )
    assert resp.status_code == 422


def test_fails_closed_when_no_keys_configured(monkeypatch):
    _patch_settings(monkeypatch, api_keys="")
    resp = client.post("/api/v1/reviews", json=INVALID_REVIEW, headers=AUTH)
    assert resp.status_code == 503
    assert "CODESAGE_API_KEYS" in resp.json()["detail"]


def test_auth_can_be_disabled_explicitly(monkeypatch):
    _patch_settings(monkeypatch, api_keys="", auth_disabled=True)
    resp = client.post("/api/v1/reviews", json=INVALID_REVIEW)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/reviews"),
        ("get", "/api/v1/reviews/00000000-0000-0000-0000-000000000000"),
        ("get", "/api/v1/reviews/00000000-0000-0000-0000-000000000000/stream"),
        ("post", "/api/v1/reviews/async"),
        ("post", "/api/v1/reviews/00000000-0000-0000-0000-000000000000/decision"),
        ("get", "/api/v1/telemetry"),
        ("post", "/api/v1/ingest"),
        ("get", "/api/v1/eval/runs"),
        ("post", "/api/v1/eval/runs"),
        ("post", "/api/v1/eval/compare"),
    ],
)
def test_every_api_route_requires_auth(method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401


def test_public_endpoints_stay_open():
    assert client.get("/healthz").status_code == 200
    meta = client.get("/api/v1/meta")
    assert meta.status_code == 200
    assert meta.json()["auth_required"] is True


def test_cors_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("CODESAGE_CORS_ORIGINS", raising=False)
    assert Settings(_env_file=None).cors_origin_list == []


# ── Ingest roots ───────────────────────────────────────────────────────────────
def test_ingest_disabled_without_roots(tmp_path):
    with pytest.raises(IngestPathError, match="CODESAGE_INGEST_ROOTS"):
        resolve_ingest_root(str(tmp_path), [])


def test_ingest_path_inside_root_is_allowed(tmp_path):
    (tmp_path / "repo").mkdir()
    assert resolve_ingest_root(str(tmp_path / "repo"), [str(tmp_path)]) == (
        tmp_path / "repo"
    ).resolve()


def test_ingest_path_outside_root_is_rejected(tmp_path):
    (tmp_path / "allowed").mkdir()
    with pytest.raises(IngestPathError, match="outside"):
        resolve_ingest_root("/", [str(tmp_path / "allowed")])


def test_ingest_dotdot_traversal_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret").mkdir()
    with pytest.raises(IngestPathError, match="outside"):
        resolve_ingest_root(str(allowed / ".." / "secret"), [str(allowed)])


def test_ingest_symlink_escape_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret").mkdir()
    (allowed / "link").symlink_to(tmp_path / "secret")
    with pytest.raises(IngestPathError, match="outside"):
        resolve_ingest_root(str(allowed / "link"), [str(allowed)])


def test_ingest_route_rejects_disallowed_path(monkeypatch):
    patched = get_settings().model_copy(update={"ingest_roots": "/nonexistent-root"})
    monkeypatch.setattr("app.api.routes.get_settings", lambda: patched)
    resp = client.post(
        "/api/v1/ingest", json={"repo": "demo/x", "path": "/etc"}, headers=AUTH
    )
    assert resp.status_code == 403


async def test_ingest_skips_symlinked_files_pointing_outside(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("x = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 'leak'\n")
    (repo / "leak.py").symlink_to(outside)

    stored: list[dict] = []

    class FakeStore:
        def __init__(self, session):
            pass

        async def clear_repo(self, repo):
            pass

        async def upsert_chunks(self, chunks):
            stored.extend(chunks)
            return len(chunks)

    monkeypatch.setattr(pipeline, "VectorStore", FakeStore)
    result = await pipeline.ingest_path(None, root=str(repo), repo="demo/x")  # type: ignore[arg-type]
    assert result["files"] == 1
    assert all("leak" not in c["content"] for c in stored)


# ── Eval datasets ──────────────────────────────────────────────────────────────
def test_bundled_dataset_resolves():
    assert resolve_dataset("eval/datasets/sample.jsonl").name == "sample.jsonl"


@pytest.mark.parametrize(
    "dataset", ["../../etc/passwd", "/etc/passwd", "eval/datasets/../../pyproject.toml"]
)
def test_dataset_outside_datasets_dir_is_rejected(dataset):
    with pytest.raises(ValueError):
        resolve_dataset(dataset)


def test_eval_route_rejects_bad_dataset():
    resp = client.post("/api/v1/eval/runs", json={"dataset": "/etc/passwd"}, headers=AUTH)
    assert resp.status_code == 422


# ── GitHub repo validation ─────────────────────────────────────────────────────
@pytest.mark.parametrize("repo", ["octocat/hello-world", "a-b/c.d_e"])
def test_valid_repos(repo):
    assert validate_repo(repo) == repo


@pytest.mark.parametrize(
    "repo", ["octocat", "a/b/c", "a/..", "../../user", "a/b?per_page=1", "a/b#x", ""]
)
def test_invalid_repos(repo):
    with pytest.raises(ValueError):
        validate_repo(repo)


async def test_github_client_validates_before_any_request():
    with pytest.raises(ValueError):
        await GitHubClient().fetch_pr_diff("../../user", 1)


def test_review_route_rejects_bad_repo_for_github_fetch():
    resp = client.post(
        "/api/v1/reviews", json={"repo": "../../user", "pr_number": 1}, headers=AUTH
    )
    assert resp.status_code == 422


# ── Sandbox ────────────────────────────────────────────────────────────────────
def test_sandbox_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CODESAGE_SANDBOX_ENABLED", raising=False)
    assert Settings(_env_file=None).sandbox_enabled is False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only")
async def test_sandbox_timeout_kills_the_whole_process_group(tmp_path):
    pids = tmp_path / "pids"
    test_src = (
        "import os, subprocess, time\n"
        "def test_hang():\n"
        "    child = subprocess.Popen(['sleep', '60'])\n"
        f"    open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
        "    time.sleep(60)\n"
    )
    diff = "--- /dev/null\n+++ b/test_hang.py\n@@ -0,0 +1,5 @@\n" + "".join(
        f"+{line}\n" for line in test_src.splitlines()
    )
    settings = get_settings().model_copy(
        update={"sandbox_enabled": True, "sandbox_timeout_s": 2}
    )

    result = await Sandbox(settings).run_tests(diff)

    assert result.ran and not result.passed
    assert "timeout" in result.summary
    pytest_pid, child_pid = map(int, pids.read_text().split())
    assert not _alive(pytest_pid)
    # The grandchild is reparented and reaped by init; allow it a moment.
    deadline = time.monotonic() + 5
    while _alive(child_pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    assert not _alive(child_pid)


# ── Logging ────────────────────────────────────────────────────────────────────
def test_logging_goes_to_stderr_not_stdout():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging("INFO")
        streams = [getattr(h, "stream", None) for h in root.handlers]
        assert sys.stderr in streams
        assert sys.stdout not in streams
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

