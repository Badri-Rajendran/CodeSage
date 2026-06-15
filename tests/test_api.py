"""API smoke tests that don't require a database."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_root_reports_stub_mode():
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "CodeSage"
    assert body["llm_mode"] == "stub"  # no API key in tests


def test_review_request_validation_requires_diff_or_pr():
    # Neither diff nor pr_number → 422 from the request model validator.
    resp = client.post("/api/v1/reviews", json={"repo": "demo/x"})
    assert resp.status_code == 422
