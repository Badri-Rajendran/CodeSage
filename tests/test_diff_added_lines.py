"""Added-line extraction, now provided by the diff model (was sandbox.extract_added_files)."""

from app.diff import parse_diff


def extract_added_files(diff: str) -> dict[str, str]:
    """Added-line content per file, via the diff model's hunks."""
    return {
        f.path: "\n".join(ln[1:] for h in f.hunks for ln in h.lines if ln.startswith("+"))
        for f in parse_diff(diff).files
    }


def test_extract_added_files():
    diff = (
        "diff --git a/test_x.py b/test_x.py\n"
        "--- a/test_x.py\n"
        "+++ b/test_x.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def test_ok():\n"
        "+    assert True\n"
    )
    files = extract_added_files(diff)
    assert "test_x.py" in files
    assert "def test_ok():" in files["test_x.py"]


def test_extract_ignores_context_and_removed_lines():
    diff = (
        "--- a/m.py\n"
        "+++ b/m.py\n"
        "@@ -1,3 +1,3 @@\n"
        " unchanged\n"
        "-removed\n"
        "+added\n"
    )
    files = extract_added_files(diff)
    assert files["m.py"] == "added"
