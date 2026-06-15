from app.eval.regression import detect_regressions


def _run(model, scores):
    return {
        "model": model,
        "mean_score": sum(scores.values()) / len(scores),
        "per_case": [{"case_id": k, "score": v} for k, v in scores.items()],
    }


def test_detects_regression():
    baseline = _run("old", {"a": 0.9, "b": 0.8, "c": 0.7})
    candidate = _run("new", {"a": 0.9, "b": 0.5, "c": 0.75})  # b regressed
    report = detect_regressions(baseline, candidate, tolerance=0.05)
    assert report.has_regression
    assert len(report.regressions) == 1
    assert report.regressions[0].case_id == "b"


def test_no_regression_within_tolerance():
    baseline = _run("old", {"a": 0.80, "b": 0.80})
    candidate = _run("new", {"a": 0.78, "b": 0.82})
    report = detect_regressions(baseline, candidate, tolerance=0.05)
    assert not report.has_regression
    assert report.as_dict()["n_regressions"] == 0


def test_improvement_tracked():
    baseline = _run("old", {"a": 0.5})
    candidate = _run("new", {"a": 0.9})
    report = detect_regressions(baseline, candidate, tolerance=0.05)
    assert len(report.improvements) == 1
    assert report.mean_delta > 0
