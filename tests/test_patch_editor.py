import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from vertical_harness.tools.patch_editor import PatchEditor
from vertical_harness.tools.update_chunk import UpdateChunk


def test_parse_operations_and_move():
    hunks = PatchEditor.parse_patch(
        "*** Begin Patch\n*** Environment ID: x\n*** Add File: a\n+new\n*** Update File: b\n*** Move to: c\n@@ section\n-old\n+new\n*** End of File\n*** Delete File: d\n*** End Patch"
    )
    assert [(hunk.type, hunk.path) for hunk in hunks] == [
        ("add", "a"),
        ("update", "b"),
        ("delete", "d"),
    ]
    assert hunks[0].contents == "new"
    assert hunks[1].move_path == "c"
    assert hunks[1].chunks == (UpdateChunk(("old",), ("new",), "section", True),)


@pytest.mark.parametrize(
    "wrapper", ["<<EOF\n{}\nEOF", "cat <<'PATCH'\n{}\nPATCH", '<<"PATCH"\n{}\nPATCH']
)
def test_heredoc(wrapper):
    assert PatchEditor.parse_patch(wrapper.format("*** Begin Patch\n*** End Patch")) == []


@pytest.mark.parametrize(
    "body",
    [
        "*** Update File: a",
        "*** Update File: a\n@@",
        "*** Add File: a\nno-prefix",
        "*** Delete File: a\n-body",
        "*** Update File: a\n*** Move to:",
        "*** Add File:",
    ],
)
def test_invalid_patch(body):
    with pytest.raises(ValueError):
        PatchEditor.parse_patch(f"*** Begin Patch\n{body}\n*** End Patch")


@pytest.mark.parametrize(
    ("original", "chunks", "expected"),
    [
        ("\ufeffold\n", (UpdateChunk(("old",), ("new",)),), "\ufeffnew\n"),
        ("  ‘old’ \n", (UpdateChunk(("'old'",), ("new",)),), "new\n"),
        ("old\r\nkeep\r\n", (UpdateChunk(("old",), ("new",)),), "new\nkeep\r\n"),
        ("", (UpdateChunk((), ("new",)),), "new\n"),
        ("old\n", (UpdateChunk(("old",), ()),), ""),
        ("a\nb\n", (UpdateChunk((), ("end",)), UpdateChunk(("a",), ("new",))), "new\nb\nend\n"),
        ("‘x’\n'x'\n", (UpdateChunk(("'x'",), ("new",)),), "‘x’\nnew\n"),
    ],
)
def test_derive(original, chunks, expected):
    assert PatchEditor.derive_update("file", chunks, original) == expected


def test_eof_does_not_fallback():
    with pytest.raises(ValueError, match="Failed to find expected"):
        PatchEditor.derive_update(
            "file", (UpdateChunk(("first",), ("new",), end_of_file=True),), "first\nlast\n"
        )


def test_source_patch_differential(tmp_path):
    reference = Path(
        os.environ.get("OPENCODE_REFERENCE", Path(__file__).resolve().parents[2] / "opencode")
    )
    source = reference / "packages/util/src/patch.ts"
    bun = shutil.which("bun")
    if not bun or not source.is_file():
        pytest.skip("Bun and the adjacent OpenCode reference checkout are required")
    bodies = [
        "",
        "*** Environment ID: x",
        "*** Add File: a\n+new",
        "*** Add File: a",
        "*** Delete File: a",
        "*** Delete File: a\n-body",
        "*** Update File: a\n@@\n-old\n+new",
        "*** Update File: a\n-old\n+new",
        "*** Update File: a\n@@\n old\n+new",
        "*** Update File: a\n@@\n-old\n+new\n*** End of File",
        "*** Update File: a\n@@ old\n+new",
        "*** Update File: a\n@@\n+new",
        "*** Update File: a\n@@\n-old\n+new\n*** End of File\n@@\n+last",
        "*** Update File: a\n*** End of File\n*** Move to: b\n old",
        "*** Update File: a\n@@\n-old\n+new\n*** End of File\n*** End of File",
        "*** Update File: a\n@@\n-old\n+new\n\n",
        "*** Update File: a\n@@\n-old\n+new\n *** Delete File: nested",
        "*** Update File: a",
        "*** Update File: a\n@@",
        "*** Add File:",
    ]
    cases = [
        {
            "patch": "*** Begin Patch\n" + (body + "\n" if body else "") + "*** End Patch",
            "original": original,
        }
        for body in bodies
        for original in ["old\n", "\ufeffold\r\n", "  old  \nlast\n", ""]
    ]
    harness = tmp_path / "source-patch.ts"
    harness.write_text(
        "const source=await Bun.file(process.argv[2]).text();\n"
        "const body=source.slice(source.indexOf('export function parse(')).replaceAll('export function ', 'function ');\n"
        "const shim=`const Result={fail:error=>({error}),succeed:value=>({value})};\n"
        "class BoundaryError extends Error { constructor(data){super(data.boundary)} }\n"
        "class InvalidHunkError extends Error { constructor(data){super(JSON.stringify(data))} }\n"
        "const Bom={split:text=>({text:text.replace(/^\\uFEFF+/,''),bom:text.startsWith('\\uFEFF')}),join:(text,bom)=>(bom?'\\uFEFF':'')+text.replace(/^\\uFEFF+/, '')};\n`;\n"
        "const run=new Function(new Bun.Transpiler({loader:'ts'}).transformSync(shim+body)+`;return c=>{const parsed=parse(c.patch);if(parsed.error)return {error:true};try{return {hunks:parsed.value,results:parsed.value.filter(h=>h.type==='update').map(h=>{const d=derive(h.path,h.chunks,c.original);return joinBom(d.content,d.bom)})}}catch{return {hunks:parsed.value,deriveError:true}}}`)();\n"
        "console.log(JSON.stringify(JSON.parse(await Bun.stdin.text()).map(run)));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [bun, str(harness), str(source)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for case, expected in zip(cases, json.loads(result.stdout), strict=True):
        if "error" in expected:
            with pytest.raises(ValueError):
                PatchEditor.parse_patch(case["patch"])
            continue
        hunks = PatchEditor.parse_patch(case["patch"])
        encoded = []
        for hunk in hunks:
            item = {"type": hunk.type, "path": hunk.path}
            if hunk.type == "add":
                item["contents"] = hunk.contents
            if hunk.type == "update":
                if hunk.move_path is not None:
                    item["movePath"] = hunk.move_path
                item["chunks"] = [
                    {
                        "oldLines": list(chunk.old_lines),
                        "newLines": list(chunk.new_lines),
                        **(
                            {"changeContext": chunk.change_context}
                            if chunk.change_context is not None
                            else {}
                        ),
                        **({"endOfFile": True} if chunk.end_of_file else {}),
                    }
                    for chunk in hunk.chunks
                ]
            encoded.append(item)
        assert encoded == expected["hunks"], case
        if expected.get("deriveError"):
            with pytest.raises(ValueError):
                [
                    PatchEditor.derive_update(hunk.path, hunk.chunks, case["original"])
                    for hunk in hunks
                    if hunk.type == "update"
                ]
        else:
            assert [
                PatchEditor.derive_update(hunk.path, hunk.chunks, case["original"])
                for hunk in hunks
                if hunk.type == "update"
            ] == expected["results"], case


def test_source_seek_randomized(tmp_path):
    reference = Path(
        os.environ.get("OPENCODE_REFERENCE", Path(__file__).resolve().parents[2] / "opencode")
    )
    source = reference / "packages/util/src/patch.ts"
    bun = shutil.which("bun")
    if not bun or not source.is_file():
        pytest.skip("Bun and the adjacent OpenCode reference checkout are required")
    rng = random.Random(3752)
    alphabet = ["a", " a", "a ", "‘x’", "'x'", "x−y", "x-y", "", "\u0085a", "\ufeffa"]
    cases = []
    for _ in range(500):
        lines = [rng.choice(alphabet) for _ in range(rng.randrange(12))]
        pattern = [rng.choice(alphabet) for _ in range(rng.randrange(5))]
        cases.append([lines, pattern, rng.randrange(len(lines) + 2), rng.choice([False, True])])
    harness = tmp_path / "source-seek.ts"
    harness.write_text(
        "const source=await Bun.file(process.argv[2]).text();\n"
        "const helpers=source.slice(source.indexOf('export function parse(')).replaceAll('export function ','function ');\n"
        "const seekFn=new Function(new Bun.Transpiler({loader:'ts'}).transformSync(helpers)+';return seek')();\n"
        "console.log(JSON.stringify(JSON.parse(await Bun.stdin.text()).map(c=>seekFn(...c))));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [bun, str(harness), str(source)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for case, expected in zip(cases, json.loads(result.stdout), strict=True):
        lines, pattern, start, eof = case
        assert PatchEditor._seek(lines, tuple(pattern), start, eof) == expected, case
