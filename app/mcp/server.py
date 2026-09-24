"""MCP server for CodeSage.

Exposes the multi-agent review pipeline as MCP tools so any MCP-compatible client
(Claude Desktop, IDE integrations, other agents) can invoke CodeSage directly:

  - review_pull_request: run the full multi-agent review on a diff
  - review_github_pr:     fetch a GitHub PR diff and review it

Run with: ``python -m app.mcp.server`` (stdio transport).
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from app.agents.graph import build_review_graph, make_deps, prepare_diff
from app.config import get_settings
from app.db.session import SessionFactory
from app.github.client import GitHubClient
from app.llm.client import LLMClient
from app.llm.telemetry import CostTracker
from app.logging_config import configure_logging
from app.rag.search import pgvector_search
from app.review_config import default_review_config
from app.workspace import Workspace

mcp = FastMCP("codesage")


async def _run_review(repo: str, diff: str, pr_number: int | None) -> dict:
    settings = get_settings()
    tracker = CostTracker()
    client = LLMClient(settings, tracker)

    cfg = default_review_config(settings)
    # Semantic search opens its own sessions and degrades gracefully if the DB is
    # unreachable (returns no results rather than failing the review).
    workspace = Workspace.diff_only(
        prepare_diff(diff, cfg), semantic=pgvector_search(SessionFactory, repo)
    )
    graph = build_review_graph(make_deps(client, workspace, settings, cfg=cfg))
    state = await graph.ainvoke({"repo": repo, "pr_number": pr_number, "diff": diff})

    return {
        "repo": repo,
        "pr_number": pr_number,
        "findings": state.get("findings", []),
        "summary": state.get("summary", ""),
        "judge_score": state.get("judge_score"),
        "requires_human_approval": state.get("requires_human_approval", False),
        "gate_reasons": state.get("gate_reasons", []),
        "telemetry": tracker.summary(),
    }


@mcp.tool()
async def review_pull_request(repo: str, diff: str, pr_number: int | None = None) -> str:
    """Review a unified diff with CodeSage's multi-agent pipeline.

    Args:
        repo: Logical repository name (owner/name).
        diff: The unified diff to review.
        pr_number: Optional PR number for context.

    Returns a JSON string with findings, summary, judge score, and telemetry.
    """
    result = await _run_review(repo, diff, pr_number)
    return json.dumps(result, indent=2)


@mcp.tool()
async def review_github_pr(repo: str, pr_number: int) -> str:
    """Fetch a GitHub pull request's diff and review it.

    Args:
        repo: Repository in owner/name form.
        pr_number: The pull request number.
    """
    diff = await GitHubClient().fetch_pr_diff(repo, pr_number)
    result = await _run_review(repo, diff, pr_number)
    return json.dumps(result, indent=2)


def main() -> None:
    configure_logging(get_settings().log_level)
    mcp.run()


if __name__ == "__main__":
    main()
