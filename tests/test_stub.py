from app.llm import stub


def _diff(added: str) -> str:
    body = "\n".join(f"+{ln}" for ln in added.splitlines())
    return f"--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n{body}\n"


def test_security_detects_command_injection():
    out = stub.generate("security", {"diff": _diff("os.system(cmd)")})
    titles = [f["title"] for f in out["findings"]]
    assert any("Command injection" in t for t in titles)
    assert out["findings"][0]["severity"] == "critical"


def test_security_detects_hardcoded_secret():
    out = stub.generate("security", {"diff": _diff('API_KEY = "sk-live-xyz"')})
    assert any("secret" in f["title"].lower() for f in out["findings"])


def test_correctness_detects_bare_except():
    out = stub.generate("correctness", {"diff": _diff("except:")})
    assert any("Bare except" in f["title"] for f in out["findings"])


def test_clean_diff_has_no_security_findings():
    out = stub.generate("security", {"diff": _diff("return a + b")})
    assert out["findings"] == []


def test_reflection_dedups():
    findings = [
        {"title": "Dup", "confidence": 0.5, "severity": "low", "rationale": "x"},
        {"title": "Dup", "confidence": 0.9, "severity": "low", "rationale": "x"},
        {"title": "Other", "confidence": 0.6, "severity": "low", "rationale": "y"},
    ]
    out = stub.generate("reflection", {"diff": "", "findings": findings})
    assert len(out["findings"]) == 2
    dup = next(f for f in out["findings"] if f["title"] == "Dup")
    assert dup["confidence"] == 0.9  # kept the higher-confidence duplicate
    assert out["summary"]


def test_judge_scores_in_range():
    out = stub.generate("judge", {"diff": "", "findings": []})
    assert 0.0 <= out["score"] <= 1.0
    assert set(out["dimensions"]) == {
        "correctness",
        "groundedness",
        "actionability",
        "signal_to_noise",
    }
