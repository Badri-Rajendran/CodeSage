"""GitHub Action runner.

    codesage-action resolve   # should this event be reviewed? which commit?
    codesage-action review    # run the review, post it, complete the check run

Use the ``codesage-action`` console script, not ``python -m app.action``, from
inside another repository: ``-m`` puts the working directory first on
``sys.path``, so a reviewed repo with its own ``app/`` package would be imported.

Both read the standard Actions environment (GITHUB_EVENT_NAME, GITHUB_EVENT_PATH,
GITHUB_REPOSITORY, GITHUB_TOKEN, GITHUB_OUTPUT, GITHUB_STEP_SUMMARY,
GITHUB_WORKSPACE). For local reproduction, pass them as flags:

    codesage-action review --event event.json --event-name pull_request \\
        --repository owner/name --workspace /path/to/checkout --dry-run

Exit codes: 0 when skipped or when a review was published (pass or fail is the
``CodeSage`` check run's job), 1 on an engine or publishing error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from app.action.rules import REVIEW_LABEL, Trigger, evaluate, is_fork
from app.agents.graph import build_review_graph, make_deps
from app.config import Settings
from app.diff import parse_diff
from app.github.client import GitHubClient, pr_context, validate_repo
from app.llm.client import LLMClient
from app.llm.telemetry import CostTracker
from app.logging_config import configure_logging, get_logger
from app.publish.github import GitHubPublisher
from app.publish.markdown import STEP_SUMMARY_LIMIT, review_body, traces_markdown, truncate
from app.review_config import ReviewConfigError, load_review_config
from app.workspace import Workspace

logger = get_logger(__name__)


# ── Actions I/O ─────────────────────────────────────────────────────────────


def _append(env_var: str, text: str) -> None:
    path = os.environ.get(env_var)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:  # local run: show what would have been written
        print(f"[{env_var}]\n{text}", file=sys.stderr)


def set_outputs(**values: Any) -> None:
    _append("GITHUB_OUTPUT", "".join(f"{k}={v}\n" for k, v in values.items()))


def step_summary(markdown: str) -> None:
    _append("GITHUB_STEP_SUMMARY", truncate(markdown, STEP_SUMMARY_LIMIT) + "\n")


# ── context ─────────────────────────────────────────────────────────────────


def _load_event(args: argparse.Namespace) -> tuple[str, dict[str, Any], str]:
    name = args.event_name or os.environ.get("GITHUB_EVENT_NAME", "")
    path = args.event or os.environ.get("GITHUB_EVENT_PATH", "")
    repository = args.repository or os.environ.get("GITHUB_REPOSITORY", "")
    if not (name and path and repository):
        raise SystemExit("error: need the event name, event payload path and repository")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return name, payload, validate_repo(repository)


async def _resolve_pr(
    trigger: Trigger, payload: dict, repository: str, gh: GitHubClient
) -> dict[str, Any] | None:
    """The PR object (from the payload, or fetched for comment triggers); None to skip."""
    pr = payload.get("pull_request")
    if pr is None:  # issue_comment: the payload has no head commit
        assert trigger.pr_number is not None
        pr = await gh.get_pr(repository, trigger.pr_number)
    if is_fork(pr, repository):
        return None
    return pr


def _local_diff(pr: dict, workspace: Path) -> str:
    return subprocess.run(
        ["git", "diff", "--no-color", f"{pr['base']['sha']}...{pr['head']['sha']}"],
        cwd=workspace, capture_output=True, text=True, check=True,
    ).stdout


async def _pr_diff(
    gh: GitHubClient, repository: str, pr: dict, workspace: Path, *, local: bool
) -> str:
    """GitHub's PR diff; for PRs too large for the API (or ``--local-diff``), the
    local three-dot diff from the checkout."""
    if local:
        return _local_diff(pr, workspace)
    try:
        return await gh.fetch_pr_diff(repository, pr["number"])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (406, 422):
            raise
        logger.warning("PR diff too large for the API; using git diff locally.")
        return _local_diff(pr, workspace)


# ── commands ────────────────────────────────────────────────────────────────


async def cmd_resolve(args: argparse.Namespace, settings: Settings) -> int:
    name, payload, repository = _load_event(args)
    trigger = evaluate(name, payload)
    if not trigger.run:
        set_outputs(run="false")
        step_summary(f"CodeSage skipped: {trigger.reason}.")
        return 0
    gh = GitHubClient(settings, token=settings.github_token)
    pr = await _resolve_pr(trigger, payload, repository, gh)
    if pr is None:
        set_outputs(run="false")
        step_summary("CodeSage skipped: pull requests from forks are not reviewed.")
        return 0
    set_outputs(run="true", head_sha=pr["head"]["sha"], pr_number=pr["number"])
    return 0


async def cmd_review(args: argparse.Namespace, settings: Settings) -> int:
    name, payload, repository = _load_event(args)
    trigger = evaluate(name, payload)
    if not trigger.run:
        step_summary(f"CodeSage skipped: {trigger.reason}.")
        return 0
    gh = GitHubClient(settings, token=settings.github_token, dry_run=args.dry_run)
    pr = await _resolve_pr(trigger, payload, repository, gh)
    if pr is None:
        step_summary("CodeSage skipped: pull requests from forks are not reviewed.")
        return 0

    ctx = pr_context(pr)
    workspace_root = Path(args.workspace or os.environ.get("GITHUB_WORKSPACE") or ".").resolve()
    publisher = GitHubPublisher(gh, repository, ctx["number"], ctx["head_sha"])
    tracker = CostTracker()
    state: dict[str, Any] = {}
    skipped: list[str] = []
    check_id: int | None = None
    try:
        check_id = await publisher.start_check()
        cfg_path = workspace_root / (args.config or os.environ.get("CODESAGE_CONFIG")
                                     or ".codesage.yml")
        cfg = load_review_config(cfg_path, settings)
        diff_text = await _pr_diff(gh, repository, pr, workspace_root, local=args.local_diff)
        kept, over = parse_diff(diff_text).filter(list(cfg.ignore_paths)).limit(cfg.max_files)
        skipped = [f.path for f in over]
        workspace = Workspace(workspace_root, kept, tests=cfg.tests)

        async def publish(final: dict) -> dict:
            url = await publisher.post_review(final, tracker.summary(), skipped_files=skipped)
            return {"github_review_url": url}

        deps = make_deps(LLMClient(settings, tracker), workspace, settings, cfg=cfg,
                         publisher=publish)
        graph = build_review_graph(deps, mode="action")
        state = await graph.ainvoke({
            "repo": repository, "pr_number": ctx["number"], "diff": diff_text, "pr": ctx,
        })
        conclusion = await publisher.finish_check(
            check_id, state, tracker.summary(), skipped_files=skipped
        )
    except Exception as exc:  # the check run must never be left "in progress"
        message = str(exc) if isinstance(exc, ReviewConfigError) else f"{type(exc).__name__}: {exc}"
        logger.exception("CodeSage review failed")
        if check_id is not None:
            try:
                await publisher.finish_check(check_id, state, tracker.summary(), error=message)
            except Exception:  # still report the original failure below
                logger.exception("Could not complete the CodeSage check run")
        step_summary(f"## CodeSage error\n\n{message}")
        set_outputs(gate="error", cost_usd=f"{tracker.total_cost:.4f}")
        return 1

    if trigger.label_trigger:
        await gh.remove_label(repository, ctx["number"], REVIEW_LABEL)
    step_summary(review_body(state, tracker.summary(), skipped_files=skipped)
                 + "\n" + traces_markdown(state))
    set_outputs(
        score="" if state.get("judge_score") is None else f"{state['judge_score']:.3f}",
        gate="tripped" if state.get("gate_tripped") else "pass",
        conclusion=conclusion,
        cost_usd=f"{tracker.total_cost:.4f}",
        review_url=state.get("github_review_url") or "",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codesage-action", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["resolve", "review"])
    parser.add_argument("--event", help="event payload JSON (default: $GITHUB_EVENT_PATH)")
    parser.add_argument("--event-name", help="default: $GITHUB_EVENT_NAME")
    parser.add_argument("--repository", help="owner/name (default: $GITHUB_REPOSITORY)")
    parser.add_argument("--workspace", help="checkout to review (default: $GITHUB_WORKSPACE)")
    parser.add_argument("--config", help="config path in the workspace (default .codesage.yml)")
    parser.add_argument("--dry-run", action="store_true", help="log GitHub writes, don't send")
    parser.add_argument("--local-diff", action="store_true",
                        help="diff base...head in the workspace instead of asking the API")
    args = parser.parse_args(argv)
    configure_logging()
    # Never read a .env file here: the working directory is the reviewed repo.
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    command = cmd_resolve if args.command == "resolve" else cmd_review
    return asyncio.run(command(args, settings))


if __name__ == "__main__":
    sys.exit(main())
