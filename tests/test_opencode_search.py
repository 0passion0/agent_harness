import asyncio
import sys

import pytest

from vertical_harness.tools import opencode_search
from vertical_harness.tools.opencode_grep_result import OpenCodeGrepResult
from vertical_harness.tools.opencode_search import RipgrepSearch


@pytest.fixture
def search(tmp_path):
    return RipgrepSearch(tmp_path, lambda path: tmp_path / path.lstrip("/"))


def test_glob_hidden_git_and_limits(search, tmp_path):
    for name in ("visible.txt", "second.txt", ".hidden.txt", ".git/config.txt"):
        target = tmp_path / name
        target.parent.mkdir(exist_ok=True)
        target.write_text("needle\n", encoding="utf-8")
    result = search.glob("**/*.txt", limit=1)
    assert len(result.matches) == 1
    assert result.truncated
    assert not result.matches[0]["path"].startswith("/.")
    result = search.glob("**/*.txt", hidden=True)
    assert {entry["path"] for entry in result.matches} == {
        "/visible.txt",
        "/second.txt",
        "/.hidden.txt",
    }


def test_grep_regex_literal_case_and_file_scope(search, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"a.b\nAxb\naab\n")
    (tmp_path / "other.txt").write_text("axb\n", encoding="utf-8")
    assert search.grep("a.b", "/a.txt").matches == [{"path": "/a.txt", "line": 1, "text": "a.b\n"}]
    matches = search.grep("a.b", "/a.txt", literal=False, case_sensitive=False).matches
    assert [match["line"] for match in matches] == [1, 2, 3]
    assert search.grep("a", max_count=1).truncated
    assert "Invalid regex pattern:" in search.grep("[", literal=False).error
    assert search.grep("").error == "Pattern must not be empty"


def test_grep_preserves_source_byte_offsets_and_submatch_limit(search, tmp_path):
    (tmp_path / "unicode.txt").write_bytes("前行\n前😀后😀\n".encode())
    result = search.grep("😀", "/unicode.txt")
    assert isinstance(result, OpenCodeGrepResult)
    assert result.source_matches == [
        {
            "entry": {"path": "/unicode.txt", "type": "file"},
            "line": 2,
            "offset": 7,
            "text": "前😀后😀\n",
            "submatches": [
                {"text": "😀", "start": 3, "end": 7},
                {"text": "😀", "start": 10, "end": 14},
            ],
        }
    ]
    assert result.matches == [{"path": "/unicode.txt", "line": 2, "text": "前😀后😀\n"}]
    (tmp_path / "unicode.txt").write_text("😀" * 101, encoding="utf-8")
    result = search.grep("😀", "/unicode.txt")
    assert len(result.source_matches[0]["submatches"]) == 100
    assert result.source_matches[0]["submatches"][-1] == {"text": "😀", "start": 396, "end": 400}
    assert result.source_matches[0]["text"] == "😀" * 101
    assert search.grep("absent", "/unicode.txt").source_matches == []
    assert search.grep("").source_matches == []


def test_grep_hidden_ignore_include_and_oversized_preview(search, tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("needle", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("needle", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("needle" + "x" * 70000, encoding="utf-8")
    result = search.grep("needle", glob="*.txt")
    assert {match["path"] for match in result.matches} == {"/ignored.txt", "/.hidden.txt"}
    preview = next(match["text"] for match in result.matches if match["path"] == "/.hidden.txt")
    assert len(preview) == 2003 and preview.endswith("...")
    assert {match["path"] for match in search.grep("needle").matches} == {"/.hidden.txt"}


def test_search_path_failures_and_containment(search, tmp_path):
    assert "does not exist" in search.glob("*", "/missing").error
    assert "does not exist" in search.grep("x", "/missing").error
    (tmp_path / "file").write_text("x", encoding="utf-8")
    assert "not a directory" in search.glob("*", "/file").error
    assert search.grep("x", "/../outside").error
    with pytest.raises(ValueError):
        search._virtual(tmp_path, "../outside")


def test_sync_search_inside_running_loop(search, tmp_path):
    (tmp_path / "file.txt").write_text("needle", encoding="utf-8")

    async def run():
        assert search.glob("*.txt").matches[0]["path"] == "/file.txt"
        assert (await search.agrep("needle")).matches[0]["line"] == 1

    asyncio.run(run())


def test_packaged_ripgrep_is_available_without_path(search, tmp_path, monkeypatch):
    (tmp_path / "file.txt").write_text("needle", encoding="utf-8")
    monkeypatch.setattr(opencode_search, "_resolve_ripgrep_path", lambda: None)
    monkeypatch.setenv("PATH", "")
    assert search.grep("needle").matches[0]["path"] == "/file.txt"


def test_async_cancellation_and_timeout_reap_process(search, monkeypatch):
    processes = []
    spawn = asyncio.create_subprocess_exec

    async def sleeping_process(*args, **kwargs):
        process = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", sleeping_process)

    async def run():
        task = asyncio.create_task(search.aglob("*"))
        while not processes:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None
        monkeypatch.setattr(opencode_search, "SEARCH_TIMEOUT", 0.05)
        result = await search.agrep("x")
        assert "timed out" in result.error
        assert processes[1].returncode is not None

    asyncio.run(run())
