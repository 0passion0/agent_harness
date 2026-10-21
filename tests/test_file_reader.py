import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vertical_harness.tools import file_reader
from vertical_harness.tools.file_reader import FileReader


@pytest.mark.parametrize(
    ("content", "mime"),
    [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF8", "image/gif"),
        (b"RIFF1234WEBP", "image/webp"),
    ],
)
def test_media_magic_without_suffix_and_with_wrong_suffix(tmp_path, content, mime):
    for name in ["no_suffix", "incorrect.txt"]:
        path = tmp_path / name
        path.write_bytes(content)
        assert FileReader.read_supported_media(path) == (
            mime,
            base64.b64encode(content).decode("ascii"),
        )


def test_media_pdf_opt_in_and_unsupported_signatures(tmp_path):
    path = tmp_path / "file.png"
    path.write_bytes(b"%PDF-1.7")
    assert FileReader.read_supported_media(path) is None
    assert FileReader.read_supported_media(path, pdf=True) == ("application/pdf", "JVBERi0xLjc=")
    for content in [b"BM", b"1234ftypavif", b"1234ftypavis", b"RIFF1234WEB", b"plain"]:
        path.write_bytes(content)
        assert FileReader.read_supported_media(path, pdf=True) is None


def test_media_limit_checks_metadata_and_actual_bytes(tmp_path, monkeypatch):
    path = tmp_path / "file"
    monkeypatch.setattr(file_reader, "MAX_MEDIA_INGEST_BYTES", 8)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert FileReader.read_supported_media(path)[0] == "image/png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nx")
    with pytest.raises(ValueError, match="8 byte limit"):
        FileReader.read_supported_media(path)
    original_fstat = os.fstat

    def stale_size(descriptor):
        result = list(original_fstat(descriptor))
        result[6] = 8
        return os.stat_result(result)

    monkeypatch.setattr(file_reader.os, "fstat", stale_size)
    with pytest.raises(ValueError, match="8 byte limit"):
        FileReader.read_supported_media(path)


def test_directory_entries_include_hidden_directories_first(tmp_path):
    (tmp_path / "z-dir").mkdir()
    (tmp_path / ".hidden-dir").mkdir()
    (tmp_path / "a-file").write_text("file", encoding="utf-8")
    (tmp_path / ".hidden").write_text("hidden", encoding="utf-8")
    assert FileReader.directory_entries(tmp_path) == [
        (".hidden-dir", True),
        ("z-dir", True),
        (".hidden", False),
        ("a-file", False),
    ]


def test_recover_read_path_normalization_and_unique_files(tmp_path):
    actual = tmp_path / "caf\u00e9\u202fowner\u2019s.txt"
    actual.write_text("content", encoding="utf-8")
    requested = tmp_path / "cafe\u0301 owner's.txt"
    assert FileReader.recover_read_path(tmp_path, requested) == actual.resolve()
    restricted = tmp_path / "restricted"
    restricted.mkdir()
    assert FileReader.recover_read_path(restricted, requested) is None
    assert FileReader.recover_read_path(tmp_path, tmp_path / "CAF\u00c9 owner's.txt") is None
    (tmp_path / "caf\u00e9\u00a0owner\u2018s.txt").write_text("ambiguous", encoding="utf-8")
    assert FileReader.recover_read_path(tmp_path, requested) is None
    assert FileReader.recover_read_path(tmp_path, tmp_path / "missing" / "file.txt") is None
    (tmp_path / "directory\u00a0name").mkdir()
    assert FileReader.recover_read_path(tmp_path, tmp_path / "directory name") is None


def test_read_helpers_symlink_types_and_containment(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "real\u00a0file").write_text("outside", encoding="utf-8")
    (root / ".hidden").write_text("hidden", encoding="utf-8")
    (root / "z-dir").mkdir()
    (root / "a-file").write_text("file", encoding="utf-8")
    try:
        (root / "escaped").symlink_to(outside, target_is_directory=True)
        (root / "broken").symlink_to(outside / "absent")
        (root / "linked\u00a0file").symlink_to(root / "a-file")
    except OSError as error:
        pytest.skip(f"Symlinks unavailable: {error}")
    assert FileReader.recover_read_path(root, root / "escaped" / "real file") is None
    assert FileReader.recover_read_path(root, root / "linked file") is None
    assert FileReader.directory_entries(root) == [
        ("z-dir", True),
        (".hidden", False),
        ("a-file", False),
        ("broken", False),
        ("escaped", False),
        ("linked\u00a0file", False),
    ]


def test_missing_read_message_suggestions_order_cap_and_discovery_errors(tmp_path, monkeypatch):
    candidates = ["ALPHA", "alpha.txt", "alpha-copy", "alpha-fourth", "unrelated"]
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(self / name for name in candidates))
    assert FileReader.missing_read_message("/folder/alpha", tmp_path / "alpha") == (
        "File not found: /folder/alpha\n\nDid you mean one of these?\n"
        "/folder/ALPHA\n/folder/alpha.txt\n/folder/alpha-copy"
    )
    assert FileReader.missing_read_message("/folder/ALPHA.txt.extra", tmp_path / "alpha") == (
        "File not found: /folder/ALPHA.txt.extra\n\nDid you mean one of these?\n"
        "/folder/ALPHA\n/folder/alpha.txt"
    )

    def unavailable(_):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", unavailable)
    assert FileReader.missing_read_message("/folder/alpha", tmp_path / "alpha") == (
        "File not found: /folder/alpha"
    )
    with pytest.raises(FileNotFoundError):
        FileReader.directory_entries(tmp_path / "missing")


def test_text_page_preserves_blank_lines_and_continuation(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("alpha\n\nomega\n", encoding="utf-8")
    page = FileReader.read_text_page(path, limit=2)
    assert page.file_data["content"] == "alpha\n\n"
    assert (page.start_line, page.end_line, page.next_offset, page.total_lines) == (1, 2, 2, None)
    final = FileReader.read_text_page(path, offset=page.next_offset)
    assert final.file_data["content"] == "omega\n"
    assert final.total_lines == 3 and final.next_offset is None


def test_text_page_line_and_byte_budgets(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("a\n" * 3000, encoding="utf-8")
    assert FileReader.read_text_page(path, limit=3000).next_offset == 2000
    path.write_text(("界" * 2000 + "\n") * 20, encoding="utf-8")
    page = FileReader.read_text_page(path)
    assert len(page.file_data["content"].encode("utf-8")) <= 50 * 1024
    assert page.next_offset == 8


def test_giant_lines_clamped_and_skipped_nul_checked(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("a" * 1000000 + "\nnext\n", encoding="utf-8")
    page = FileReader.read_text_page(path, limit=1)
    assert page.file_data["content"] == "a" * 2000 + "... (line truncated to 2000 chars)\n"
    assert page.next_offset == 1
    path.write_text("\0\nvalid\n", encoding="utf-8")
    assert "Binary file" in FileReader.read_text_page(path, offset=1).error
    path.write_text("valid\n\0\n", encoding="utf-8")
    assert FileReader.read_text_page(path, limit=1).file_data["content"] == "valid\n"


def test_existing_utf8_bom_zero_empty_and_range_contract(tmp_path):
    path = tmp_path / "file.txt"
    path.write_bytes(b"\xef\xbb\xbfalpha\r\n")
    assert FileReader.read_text_page(path).file_data["content"] == "alpha\n"
    assert FileReader.read_text_page(path, limit=0).no_lines_requested
    assert "exceeds file length" in FileReader.read_text_page(path, offset=3).error
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        FileReader.read_text_page(path)
    path.write_text("", encoding="utf-8")
    page = FileReader.read_text_page(path)
    assert page.total_lines is None and page.start_line is None


def test_line_clamp_counts_utf16_units(tmp_path):
    path = tmp_path / "unicode.txt"
    path.write_text("😀" * 1001 + "\n", encoding="utf-8")
    assert (
        FileReader.read_text_page(path).file_data["content"]
        == "😀" * 1000 + "... (line truncated to 2000 chars)\n"
    )
    path.write_text("a" * 1999 + "😀\n", encoding="utf-8")
    assert (
        FileReader.read_text_page(path).file_data["content"]
        == "a" * 1999 + "\ud83d... (line truncated to 2000 chars)\n"
    )


def test_pinned_opencode_text_page_differential(tmp_path):
    source_root = Path(
        os.environ.get("OPENCODE_SOURCE_ROOT", Path(__file__).resolve().parents[2] / "opencode")
    )
    source = source_root / "packages/core/src/tool/read-filesystem.ts"
    bun = shutil.which("bun")
    if not source.is_file() or bun is None:
        pytest.skip("Pinned OpenCode checkout and Bun required; set OPENCODE_SOURCE_ROOT")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source_root, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert revision == "7d3752c14ce8dd03cfaac03ee4062a877808a69c"
    # Offset and terminal newlines stay backend-compatible; the source provides page selection/clamping.
    cases = [
        {"text": "alpha\n\nomega\n", "offset": 0, "limit": 2},
        {"text": "before\nalpha\n\n\nomega\n", "offset": 1, "limit": 3},
        {"text": "alpha\r\nbeta\r\n", "offset": 1, "limit": 2},
        {"text": "😀" * 1001 + "\nnext\n", "offset": 0, "limit": 1},
        {"text": "a" * 1999 + "😀\n", "offset": 0, "limit": 1},
        {"text": "a\n" * 3000, "offset": 0, "limit": 3000},
        {"text": "界" * 2000 + "\n" + ("界" * 2000 + "\n") * 19, "offset": 0, "limit": 2000},
    ]
    script = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const source = fs.readFileSync(input.source, "utf8");
let body = source.split("const textPage = ")[1].split("// A trailing newline")[0];
body = body.replace("(bytes: Uint8Array, eof: boolean, page: PageInput)", "(bytes, eof, page)")
 .replace("const entries: string[]", "const entries").replace("let next: number | undefined", "let next")
 .replace("(line: string)", "(line)");
const newline = source.split("const nthNewline = ")[1].split("const layer =")[0]
 .replace("(bytes: Uint8Array, count: number)", "(bytes, count)");
const page = new Function("Buffer", 'const MAX_READ_LINES=2000, MAX_READ_BYTES=51200, MAX_LINE_LENGTH=2000, MAX_LINE_SUFFIX="... (line truncated to 2000 chars)"; const nthNewline = ' + newline + '; const textPage = ' + body + '; return textPage;')(Buffer);
console.log(JSON.stringify(input.cases.map(value => page(Buffer.from(value.text), true, {offset:value.offset+1,limit:value.limit}))));
"""
    completed = subprocess.run(
        [bun, "-e", script],
        input=json.dumps({"source": str(source), "cases": cases}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for index, (case, expected) in enumerate(zip(cases, json.loads(completed.stdout), strict=True)):
        path = tmp_path / f"case{index}.txt"
        # utf-8-sig deliberately removes BOM before the source-compatible page algorithm.
        path.write_bytes(b"\xef\xbb\xbf" + case["text"].encode("utf-8"))
        actual = FileReader.read_text_page(path, offset=case["offset"], limit=case["limit"])
        original_lines = case["text"].replace("\r\n", "\n").splitlines(keepends=True)
        retained_lines = original_lines[case["offset"] : case["offset"] + len(expected["entries"])]
        retained_newline = "\n" if retained_lines and retained_lines[-1].endswith("\n") else ""
        assert actual.file_data["content"] == "\n".join(expected["entries"]) + retained_newline
        assert actual.next_offset == (expected["next"] - 1 if "next" in expected else None)
