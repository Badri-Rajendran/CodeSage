from app.rag.chunker import chunk_file, language_for


def test_language_detection():
    assert language_for("a/b/foo.py") == "python"
    assert language_for("foo.ts") == "typescript"
    assert language_for("foo.unknown") is None


def test_chunk_file_windows_with_overlap():
    text = "\n".join(f"line {i}" for i in range(150))
    chunks = chunk_file("demo/repo", "x.py", text, window=60, overlap=12)
    assert len(chunks) >= 2
    first = chunks[0]
    assert first.start_line == 1
    assert first.language == "python"
    # Overlap: second chunk starts before the first one ends.
    assert chunks[1].start_line < first.end_line


def test_chunk_file_empty():
    assert chunk_file("r", "x.py", "") == []
    assert chunk_file("r", "x.py", "   \n  \n") == []
