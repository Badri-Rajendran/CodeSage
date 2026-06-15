"""Minimal GitHub client to fetch a pull request's unified diff."""

from __future__ import annotations

import httpx

from app.config import Settings, get_settings


class GitHubClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def fetch_pr_diff(self, repo: str, pr_number: int) -> str:
        """Fetch the unified diff for `owner/name#pr_number`.

        Uses the REST endpoint with the `diff` media type. A token raises the
        rate limit and is required for private repos.
        """
        headers = {
            "Accept": "application/vnd.github.v3.diff",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"

        url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
