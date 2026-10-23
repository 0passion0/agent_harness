"""Write byte contracts compared with the pinned OpenCode source helpers."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vertical_harness.filesystem import file_mutation
from vertical_harness.filesystem.document_backend import DocumentFilesystemBackend

_REFERENCE_COMMIT = "7d3752c14ce8dd03cfaac03ee4062a877808a69c"


def test_write_source_differential(tmp_path):
    reference = Path(
        os.environ.get("OPENCODE_REFERENCE", Path(__file__).resolve().parents[2] / "opencode")
    )
    bom_source = reference / "packages/util/src/bom.ts"
    mutation_source = reference / "packages/core/src/file-mutation.ts"
    bun = shutil.which("bun")
    if not bun or not bom_source.is_file() or not mutation_source.is_file():
        pytest.skip("Bun and the adjacent OpenCode reference checkout are required")
    commit = subprocess.run(
        ["git", "-C", str(reference), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    assert commit == _REFERENCE_COMMIT
    cases = [
        {"initial": None, "writes": ["first\n", "second\r\n", "second\r\n"]},
        {"initial": None, "writes": ["\ufeff\ufefffirst\r\n", "next\n", ""]},
        {"initial": "old\r\n", "writes": ["new\n", "\ufeffnew\r\n", "plain\n"]},
        {"initial": "\ufeffold\r\n", "writes": ["new\n", "new\n", "\ufeff\ufefflast"]},
        {"initial": "\ufeff\ufeffold\n", "writes": ["\ufeff\ufeffnext\n", ""]},
        {"initial": "", "writes": ["", "\ufeff", "\ufeff\ufeff", "tail"]},
        {"initial": None, "writes": ["a\r\nb\nc\r\n", "a\nb\r\nc\n", "no final LF"]},
        {"initial": "old", "writes": ["中文 ‘quotes’\r\n\ufeffbody\n", "\ufeff\ufeffdone\r\n"]},
    ]
    harness = tmp_path / "source-write.ts"
    harness.write_text(
        r"""const [bomPath, mutationPath] = Bun.argv.slice(2);
const source = await Bun.file(bomPath).text();
// Execute actual split/join/has, excluding Effect imports and later effectful helpers.
const helpers = source.slice(source.indexOf('const code ='), source.indexOf('export function decodeBytes'));
const Bom = new Function(new Bun.Transpiler({loader:'ts'}).transformSync(helpers.replaceAll('export ', '')) + ';return {split,join,has}')();
const mutation = await Bun.file(mutationPath).text();
const body = mutation.slice(mutation.indexOf('const writeTextPreservingBom ='), mutation.indexOf('return Service.of'));
const next = body.match(/const next = Bom\.split\(input\.content\)/g);
const encode = body.match(/new TextEncoder\(\)\.encode\(Bom\.join[^\n]+/g);
if (next?.length !== 1 || encode?.length !== 1 || !body.includes('{ offset: 0, length: 3 }')) throw new Error('Source write helper extraction changed');
// The source reads three existing bytes after splitting input, then encodes this expression.
const write = new Function('Bom', 'input', 'readCurrent', `${next[0]};const current=readCurrent();return ${encode[0].trimEnd().replace(/,$/, '')}`);
const cases = await Bun.stdin.json();
const results = cases.map(c => {
  let bytes = c.initial === null ? undefined : new TextEncoder().encode(c.initial);
  return c.writes.map(content => {
    bytes = write(Bom, {content}, () => bytes?.slice(0, 3));
    return Array.from(bytes);
  });
});
console.log(JSON.stringify(results));
""",
        encoding="utf-8",
    )
    expected = json.loads(
        subprocess.run(
            [bun, str(harness), str(bom_source), str(mutation_source)],
            input=json.dumps(cases),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = DocumentFilesystemBackend(root_dir=str(workspace), virtual_mode=True)
    for index, (case, outputs) in enumerate(zip(cases, expected, strict=True)):
        target = workspace / f"case{index}" / "parents" / "file.txt"
        if case["initial"] is not None:
            target.parent.mkdir(parents=True)
            target.write_bytes(case["initial"].encode("utf-8"))
        for content, output in zip(case["writes"], outputs, strict=True):
            result = backend.write(f"/case{index}/parents/file.txt", content)
            assert result.error is None
            assert result.path == f"/case{index}/parents/file.txt"
            assert target.read_bytes() == bytes(output), (case, content)
        assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure_stage", ["fsync", "replace"])
def test_write_failure_keeps_original_and_removes_temporary(
    tmp_path, monkeypatch, existing, failure_stage
):
    """Target atomic-write safety; this is an extra guarantee, not a source differential."""
    target = tmp_path / "file.txt"
    original = b"\xef\xbb\xbforiginal\r\n"
    if existing:
        target.write_bytes(original)
    backend = DocumentFilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)

    def fail(*args):
        raise OSError("injected write failure")

    monkeypatch.setattr(file_mutation.os, failure_stage, fail)
    result = backend.write("/file.txt", "\ufeffreplacement\n")
    assert result.error == "Write failed: injected write failure"
    assert result.path is None
    if existing:
        assert target.read_bytes() == original
        assert list(tmp_path.iterdir()) == [target]
    else:
        assert not target.exists()
        assert list(tmp_path.iterdir()) == []
