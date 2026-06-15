"""Cross-version regression detection.

Compares two eval runs (e.g. an old model vs a candidate model) case-by-case and
flags cases where the candidate's judge score drops by more than a tolerance. Also
reports aggregate movement so a model upgrade can be gated on "no significant
regression".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CaseDelta:
    case_id: str
    baseline_score: float
    candidate_score: float

    @property
    def delta(self) -> float:
        return self.candidate_score - self.baseline_score


@dataclass
class RegressionReport:
    baseline_model: str
    candidate_model: str
    baseline_mean: float
    candidate_mean: float
    tolerance: float
    deltas: list[CaseDelta]

    @property
    def mean_delta(self) -> float:
        return round(self.candidate_mean - self.baseline_mean, 4)

    @property
    def regressions(self) -> list[CaseDelta]:
        return [d for d in self.deltas if d.delta < -self.tolerance]

    @property
    def improvements(self) -> list[CaseDelta]:
        return [d for d in self.deltas if d.delta > self.tolerance]

    @property
    def has_regression(self) -> bool:
        return len(self.regressions) > 0

    def as_dict(self) -> dict:
        return {
            "baseline_model": self.baseline_model,
            "candidate_model": self.candidate_model,
            "baseline_mean": round(self.baseline_mean, 4),
            "candidate_mean": round(self.candidate_mean, 4),
            "mean_delta": self.mean_delta,
            "tolerance": self.tolerance,
            "n_cases": len(self.deltas),
            "n_regressions": len(self.regressions),
            "n_improvements": len(self.improvements),
            "has_regression": self.has_regression,
            "regressions": [
                {
                    "case_id": d.case_id,
                    "baseline": round(d.baseline_score, 4),
                    "candidate": round(d.candidate_score, 4),
                    "delta": round(d.delta, 4),
                }
                for d in self.regressions
            ],
        }


def detect_regressions(
    baseline: dict,
    candidate: dict,
    *,
    tolerance: float = 0.05,
) -> RegressionReport:
    """Build a regression report from two run results.

    Each run dict is `{"model": str, "mean_score": float, "per_case": [{"case_id", "score"}]}`.
    """
    base_by_id = {c["case_id"]: c["score"] for c in baseline.get("per_case", [])}
    cand_by_id = {c["case_id"]: c["score"] for c in candidate.get("per_case", [])}

    deltas = [
        CaseDelta(cid, base_by_id[cid], cand_by_id[cid])
        for cid in base_by_id
        if cid in cand_by_id
    ]

    return RegressionReport(
        baseline_model=baseline.get("model", "baseline"),
        candidate_model=candidate.get("model", "candidate"),
        baseline_mean=baseline.get("mean_score", 0.0),
        candidate_mean=candidate.get("mean_score", 0.0),
        tolerance=tolerance,
        deltas=deltas,
    )
