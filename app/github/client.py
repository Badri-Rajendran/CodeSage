"""Minimal GitHub client to fetch a pull request's unified diff."""

from __future__ import annotations

import re

import httpx

from app.config import Settings, get_settings

# owner/name as GitHub allows it. Anything else (slashes, "..", query strings)
# could steer the token-bearing request at a different API endpoint.
_REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")


def validate_repo(repo: str) -> str:
    if not _REPO_RE.fullmatch(repo) or repo.split("/")[1] in (".", ".."):
        raise ValueError(f"Invalid GitHub repository {repo!r}; expected owner/name.")
    return repo


class GitHubClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def fetch_pr_diff(self, repo: str, pr_number: int) -> str:
        """Fetch the unified diff for `owner/name#pr_number`.

        Uses the REST endpoint with the `diff` media type. A token raises the
        rate limit and is required for private repos.
        """
        validate_repo(repo)
        if pr_number < 1:
            raise ValueError(f"Invalid pull request number: {pr_number}")
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
