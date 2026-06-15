"""LLM-as-Judge eval harness + cross-version regression CLI.

Usage:
    python -m app.eval.harness run --dataset eval/datasets/sample.jsonl
    python -m app.eval.harness compare --baseline claude-opus-4-7 \
        --candidate claude-opus-4-8 --dataset eval/datasets/sample.jsonl

A dataset is a JSONL file where each line is {"case_id", "repo", "diff"}.
Runs the full review graph (without persistence) per case and records the judge
score. `compare` runs the dataset under two models and prints a regression report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.agents.graph import build_review_graph, make_deps
from app.agents.tools import ReviewTools
from app.config import get_settings
from app.eval.regression import detect_regressions
from app.llm.client import LLMClient
from app.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

RESULTS_DIR = Path("eval/results")


def load_dataset(path: str) -> list[dict]:
    cases = []
    for i, line in enumerate(Path(path).read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        obj.setdefault("case_id", obj.get("repo", f"case-{i}"))
        cases.append(obj)
    return cases


async def _score_case(case: dict, model: str) -> float:
    settings = get_settings().model_copy(update={"model": model, "judge_model": model})
    client = LLMClient(settings)
    # No session factory in the eval harness → RAG retrieval is skipped gracefully.
    tools = ReviewTools(case["repo"], case["diff"], settings=settings)
    graph = build_review_graph(make_deps(client, tools, settings))
    state = await graph.ainvoke(
        {"repo": case["repo"], "pr_number": case.get("pr_number"), "diff": case["diff"]}
    )
    return float(state.get("judge_score", 0.0))


async def run_dataset(dataset: str, model: str) -> dict:
    cases = load_dataset(dataset)
    per_case = []
    for case in cases:
        score = await _score_case(case, model)
        per_case.append({"case_id": case["case_id"], "score": round(score, 4)})
        logger.info("  %-30s score=%.3f", case["case_id"], score)
    mean = sum(c["score"] for c in per_case) / len(per_case) if per_case else 0.0
    return {
        "dataset": dataset,
        "model": model,
        "mean_score": round(mean, 4),
        "n_cases": len(per_case),
        "per_case": per_case,
    }


def _save(result: dict, name: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(name.encode()).hexdigest()[:8]
    out = RESULTS_DIR / f"{name.replace('/', '_').replace(':', '_')}_{digest}.json"
    out.write_text(json.dumps(result, indent=2))
    return out


async def _cmd_run(args: argparse.Namespace) -> None:
    settings = get_settings()
    model = args.model or settings.model
    logger.info("Running eval on %s with model %s", args.dataset, model)
    result = await run_dataset(args.dataset, model)
    out = _save(result, f"run_{model}")
    print(json.dumps(result, indent=2))
    print(f"\nSaved → {out}")


async def _cmd_compare(args: argparse.Namespace) -> None:
    logger.info("Comparing %s (baseline) vs %s (candidate)", args.baseline, args.candidate)
    baseline = await run_dataset(args.dataset, args.baseline)
    candidate = await run_dataset(args.dataset, args.candidate)
    _save(baseline, f"baseline_{args.baseline}")
    _save(candidate, f"candidate_{args.candidate}")
    report = detect_regressions(baseline, candidate, tolerance=args.tolerance)
    print(json.dumps(report.as_dict(), indent=2))
    if report.has_regression:
        print(f"\n❌ {len(report.regressions)} regression(s) detected.")
        raise SystemExit(1)
    print("\n✅ No significant regressions.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codesage-eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Score a dataset with the judge")
    p_run.add_argument("--dataset", default="eval/datasets/sample.jsonl")
    p_run.add_argument("--model", default=None)
    p_run.set_defaults(func=_cmd_run)

    p_cmp = sub.add_parser("compare", help="Compare two models and flag regressions")
    p_cmp.add_argument("--dataset", default="eval/datasets/sample.jsonl")
    p_cmp.add_argument("--baseline", required=True)
    p_cmp.add_argument("--candidate", required=True)
    p_cmp.add_argument("--tolerance", type=float, default=0.05)
    p_cmp.set_defaults(func=_cmd_compare)

    return parser


def main() -> None:
    configure_logging(get_settings().log_level)
    args = build_parser().parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
