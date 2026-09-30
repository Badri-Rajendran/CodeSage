"""GitHub REST client: read pull requests, post reviews and check runs.

Every call validates the repository name first (``validate_repo``), so a
crafted name can't steer the token-bearing request at another endpoint.

``dry_run`` sends reads normally but only logs writes (to stderr) and returns
placeholder responses. ``python -m app.action review --dry-run`` uses it to
reproduce an Action run locally without posting anything.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings

# owner/name as GitHub allows it. Anything else (slashes, "..", query strings)
# could steer the token-bearing request at a different API endpoint.
_REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_API_VERSION = "2022-11-28"
ANNOTATIONS_PER_REQUEST = 50  # GitHub's per-request limit for check-run annotations


def validate_repo(repo: str) -> str:
    if not _REPO_RE.fullmatch(repo) or repo.split("/")[1] in (".", ".."):
        raise ValueError(f"Invalid GitHub repository {repo!r}; expected owner/name.")
    return repo


def pr_context(pr: dict[str, Any]) -> dict[str, Any]:
    """The PR fields the review engine uses, from a GitHub pull-request object."""
    return {
        "number": pr["number"],
        "title": pr.get("title") or "",
        "body": pr.get("body") or "",
        "base_sha": pr["base"]["sha"],
        "head_sha": pr["head"]["sha"],
        "url": pr.get("html_url"),
    }


def _validate_number(n: int, what: str = "pull request number") -> int:
    if not isinstance(n, int) or n < 1:
        raise ValueError(f"Invalid {what}: {n!r}")
    return n


class GitHubClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        token: str | None = None,
        dry_run: bool = False,
    ):
        self.settings = settings or get_settings()
        self.token = token if token is not None else self.settings.github_token
        self.api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.dry_run = dry_run
        self._fake_id = 0

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> httpx.Response:
        if self.dry_run and method != "GET":
            return self._fake(method, path, json_body)
        headers = {"Accept": accept, "X-GitHub-Api-Version": _API_VERSION}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.request(
                method, f"{self.api_url}{path}", headers=headers, json=json_body, params=params
            )
        resp.raise_for_status()
        return resp

    def _fake(self, method: str, path: str, body: dict[str, Any] | None) -> httpx.Response:
        self._fake_id += 1
        preview = json.dumps(body, indent=1)[:4000] if body else ""
        print(f"[dry-run] {method} {path}\n{preview}", file=sys.stderr)
        fake = {"id": self._fake_id, "html_url": f"https://github.com/dry-run{path}"}
        return httpx.Response(
            200, json=fake, request=httpx.Request(method, f"{self.api_url}{path}")
        )

    # ── pull requests ───────────────────────────────────────────────────────

    async def fetch_pr_diff(self, repo: str, pr_number: int) -> str:
        """The PR's unified diff (``application/vnd.github.v3.diff``)."""
        validate_repo(repo)
        _validate_number(pr_number)
        resp = await self._request(
            "GET", f"/repos/{repo}/pulls/{pr_number}", accept="application/vnd.github.v3.diff"
        )
        return resp.text

    async def get_pr(self, repo: str, pr_number: int) -> dict[str, Any]:
        """PR metadata: title, body, head/base SHAs, head repo, draft flag, url."""
        validate_repo(repo)
        _validate_number(pr_number)
        return (await self._request("GET", f"/repos/{repo}/pulls/{pr_number}")).json()

    async def list_reviews(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        validate_repo(repo)
        _validate_number(pr_number)
        reviews: list[dict[str, Any]] = []
        for page in range(1, 11):
            batch = (await self._request(
                "GET", f"/repos/{repo}/pulls/{pr_number}/reviews",
                params={"per_page": 100, "page": page},
            )).json()
            reviews.extend(batch)
            if len(batch) < 100:
                break
        return reviews

    async def create_review(
        self,
        repo: str,
        pr_number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Post one ``COMMENT`` review; inline comments use ``line``/``side``."""
        validate_repo(repo)
        _validate_number(pr_number)
        payload: dict[str, Any] = {"commit_id": commit_id, "body": body, "event": "COMMENT"}
        if comments:
            payload["comments"] = comments
        resp = await self._request(
            "POST", f"/repos/{repo}/pulls/{pr_number}/reviews", json_body=payload
        )
        return resp.json()

    async def remove_label(self, repo: str, pr_number: int, label: str) -> None:
        validate_repo(repo)
        _validate_number(pr_number)
        try:
            await self._request(
                "DELETE", f"/repos/{repo}/issues/{pr_number}/labels/{quote(label, safe='')}"
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:  # already removed
                raise

    # ── check runs ──────────────────────────────────────────────────────────

    async def create_check_run(
        self, repo: str, *, name: str, head_sha: str, title: str, summary: str
    ) -> dict[str, Any]:
        validate_repo(repo)
        payload = {
            "name": name,
            "head_sha": head_sha,
            "status": "in_progress",
            "output": {"title": title, "summary": summary},
        }
        return (await self._request("POST", f"/repos/{repo}/check-runs", json_body=payload)).json()

    async def update_check_run(
        self,
        repo: str,
        check_run_id: int,
        *,
        conclusion: str,
        title: str,
        summary: str,
        text: str | None = None,
        annotations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Complete a check run. Annotations go in batches of 50 (GitHub's limit);
        each update appends, and the last one carries the conclusion."""
        validate_repo(repo)
        _validate_number(check_run_id, "check run id")
        annotations = annotations or []
        batches = [
            annotations[i : i + ANNOTATIONS_PER_REQUEST]
            for i in range(0, len(annotations), ANNOTATIONS_PER_REQUEST)
        ] or [[]]
        output: dict[str, Any] = {"title": title, "summary": summary}
        if text:
            output["text"] = text
        result: dict[str, Any] = {}
        for i, batch in enumerate(batches):
            payload: dict[str, Any] = {"output": {**output, "annotations": batch}}
            if i == len(batches) - 1:
                payload.update(status="completed", conclusion=conclusion)
            result = (await self._request(
                "PATCH", f"/repos/{repo}/check-runs/{check_run_id}", json_body=payload
            )).json()
        return result
