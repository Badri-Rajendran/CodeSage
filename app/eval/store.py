"""Read/launch helpers for the eval harness, used by the REST API.

The CLI harness (``app.eval.harness``) writes run results as JSON files under
``eval/results``. The dashboard lists those files and can launch new runs or
cross-version comparisons as background tasks (the heavy lifting reuses
``run_dataset`` / ``detect_regressions`` unchanged).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.eval.harness import RESULTS_DIR, run_dataset
from app.eval.regression import detect_regressions
from app.logging_config import get_logger

logger = get_logger(__name__)


def _kind_for(name: str) -> str:
    if name.startswith("compare_"):
        return "compare"
    if name.startswith(("baseline_", "candidate_")):
        return "comparand"
    return "run"


def list_results() -> list[dict[str, Any]]:
    """Return saved eval result files, newest first."""
    if not RESULTS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in RESULTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "name": path.stem,
                "kind": _kind_for(path.name),
                "modified": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=UTC
                ).isoformat(),
                "data": data,
            }
        )
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


def _save(result: dict[str, Any], name: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = name.replace("/", "_").replace(":", "_")
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{safe}_{stamp}.json"
    out.write_text(json.dumps(result, indent=2))
    return out


async def run_eval(dataset: str, model: str) -> dict[str, Any]:
    logger.info("Eval run launched: dataset=%s model=%s", dataset, model)
    result = await run_dataset(dataset, model)
    _save(result, f"run_{model}")
    return result


async def compare_eval(
    dataset: str, baseline: str, candidate: str, tolerance: float
) -> dict[str, Any]:
    logger.info("Eval compare launched: %s vs %s", baseline, candidate)
    base = await run_dataset(dataset, baseline)
    cand = await run_dataset(dataset, candidate)
    report = detect_regressions(base, cand, tolerance=tolerance).as_dict()
    report["dataset"] = dataset
    _save(report, f"compare_{baseline}_vs_{candidate}")
    return report


def launch_run(dataset: str, model: str) -> None:
    """Fire-and-forget an eval run as a background task."""
    asyncio.create_task(_guarded(run_eval(dataset, model), "eval run"))


def launch_compare(dataset: str, baseline: str, candidate: str, tolerance: float) -> None:
    asyncio.create_task(
        _guarded(compare_eval(dataset, baseline, candidate, tolerance), "eval compare")
    )


async def _guarded(coro: Any, label: str) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001 — background task; log and move on
        logger.exception("%s failed", label)
