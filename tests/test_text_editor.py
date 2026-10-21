import json
import os
import random
import shutil
import subprocess
from itertools import product
from pathlib import Path

import pytest

from vertical_harness.tools.text_editor import TextEditor


@pytest.mark.parametrize(
    ("content", "old", "new", "all_matches", "expected"),
    [
        ("one one", "one", "two", True, "two two"),
        ("‘old’ — x", "'old' -", "new", False, "new x"),
        ("old  \nkeep\n", "old\n", "new\n", False, "new\nkeep\n"),
        ("\ufeff\ufeffold\r\nnext\r\n", "old\nnext", "new\nnext", False, "\ufeffnew\r\nnext\r\n"),
        ("a\r\nb\nc\r\n", "a\nb\nc", "a\nx\nc", False, "a\r\nx\nc\r\n"),
        ("a\r\nb\nc\r\n", "a\nb\nc", "a\ninsert\nb\nc", False, "a\r\ninsert\r\nb\nc\r\n"),
        ("a\r\nb\n", "a\r\n", "a\n", False, "a\nb\n"),
        ("‘x’ 'x'", "'x'", "y", False, "‘x’ y"),
        ("\u0085a\u0085", "a", "b", False, "\u0085b\u0085"),
    ],
)
def test_replacement(content, old, new, all_matches, expected):
    assert TextEditor.replace_text(content, old, new, all_matches) == expected


@pytest.mark.parametrize(
    ("content", "old", "new", "error"),
    [
        ("a", "", "b", "must not be empty"),
        ("a", "a", "a", "identical"),
        ("a a", "a", "b", "Found 2 matches"),
        ("a", "missing", "b", "Could not find"),
        ("a\r\n", "a\n", "a\r\n", "Could not find"),
        ("x\r\ny", "x\ny", "x\r\ny", "Could not find"),
    ],
)
def test_replacement_errors(content, old, new, error):
    with pytest.raises(ValueError, match=error):
        TextEditor.replace_text(content, old, new)


def test_replacement_count():
    assert TextEditor.replace_text_with_count("a a a", "a", "b", True) == ("b b b", 3)


def test_source_edit_differential(tmp_path):
    reference = Path(
        os.environ.get("OPENCODE_REFERENCE", Path(__file__).resolve().parents[2] / "opencode")
    )
    source = reference / "packages/core/src/tool/plugin/edit.ts"
    bun = shutil.which("bun")
    if not bun or not source.is_file():
        pytest.skip("Bun and the adjacent OpenCode reference checkout are required")
    cases = [
        {"content": content, "old": old, "new": new, "all": all_matches}
        for content in [
            "a\nb\nc\n",
            "a\r\nb\r\nc\r\n",
            "a\r\nb\nc\r\n",
            "a\na\n",
            "a  \nb\n",
            "‘a’ — b\n",
            "\ufeffa\r\nb\n",
        ]
        for old, new in [
            ("a", "x"),
            ("a\nb", "a\nnew\nb"),
            ("a\nb", "x\ny"),
            ("'a' - b", "new"),
            ("a\n", "x\n"),
            ("a\r\n", "a\n"),
        ]
        for all_matches in [False, True]
    ]
    # Execute the reference's private pure helpers without loading its runtime service graph.
    harness = tmp_path / "source-edit.ts"
    harness.write_text(
        "const source = await Bun.file(process.argv[2]).text();\n"
        "const helpers = source.slice(source.indexOf('const crlf ='), source.indexOf('/** Deferred edit'));\n"
        "const run = new Function(new Bun.Transpiler({loader:'ts'}).transformSync(helpers) + String.raw`\n"
        "return (content, old, next, all) => {\n"
        "const text=content.replace(/^\\uFEFF+/, '');\n"
        "if(old===next || old==='') throw Error();\n"
        "const only=old.replaceAll('\\r','')===next.replaceAll('\\r','');\n"
        "const {matches,replace}=only?{matches:findOccurrences(text,old),replace:()=>next}:matchView(text,old,next);\n"
        "if(!matches.length || (matches.length>1&&!all)) throw Error();\n"
        "const result=(all?matches:matches.slice(0,1)).toReversed().reduce((s,m)=>s.slice(0,m.start)+replace(m)+s.slice(m.end),text);\n"
        "if(result===text) throw Error();\n"
        "return (content.startsWith('\\uFEFF')||result.startsWith('\\uFEFF')?'\\uFEFF':'')+result.replace(/^\\uFEFF+/, '');\n"
        "}`)();\n"
        "const cases=JSON.parse(await Bun.stdin.text());\n"
        "console.log(JSON.stringify(cases.map(c=>{try{return {value:run(c.content,c.old,c.new,c.all)}}catch{return {error:true}}})));\n",
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
                TextEditor.replace_text(case["content"], case["old"], case["new"], case["all"])
        else:
            assert (
                TextEditor.replace_text(case["content"], case["old"], case["new"], case["all"])
                == expected["value"]
            ), case


def test_source_line_alignment_differential(tmp_path):
    reference = Path(
        os.environ.get("OPENCODE_REFERENCE", Path(__file__).resolve().parents[2] / "opencode")
    )
    source = reference / "packages/core/src/tool/plugin/edit.ts"
    bun = shutil.which("bun")
    if not bun or not source.is_file():
        pytest.skip("Bun and the adjacent OpenCode reference checkout are required")
    rng = random.Random(3752)
    sequences = [list(items) for length in range(6) for items in product(["a", "b"], repeat=length)]
    cases = [[before, after] for before in sequences for after in sequences]
    cases += [[[*before, "tail", "tail"], [*after, "tail", "tail"]] for before, after in cases]
    cases += [
        [["a", "b"], ["b", "b"]],
        [["a", "b", "a"], ["b", "a", "a"]],
        [["old", "one", "two", "tail"], ["new", "tail"]],
        [["old", "tail"], ["new", "one", "two", "tail"]],
    ]
    for _ in range(500):
        prefix = [rng.choice(["a", "b", ""]) for _ in range(rng.randrange(6))]
        suffix = [rng.choice(["a", "b", "", "tail"]) for _ in range(rng.randrange(6))]
        cases.append(
            [
                prefix + [rng.choice(["a", "b", ""]) for _ in range(rng.randrange(10))] + suffix,
                prefix + [rng.choice(["a", "b", ""]) for _ in range(rng.randrange(10))] + suffix,
            ]
        )
    harness = tmp_path / "source-alignment.ts"
    harness.write_text(
        "const source=await Bun.file(process.argv[2]).text();\n"
        "const helpers=source.slice(source.indexOf('const crlf ='),source.indexOf('/** Deferred edit'));\n"
        "const {alignLines,withRegionEndings}=new Function(new Bun.Transpiler({loader:'ts'}).transformSync(helpers)+';return {alignLines,withRegionEndings}')();\n"
        "console.log(JSON.stringify(JSON.parse(await Bun.stdin.text()).map(([before,after])=>{\n"
        "const region=before.map((line,i)=>line+(i<before.length-1?(i%2?'\\n':'\\r\\n'):'')).join('');\n"
        "return [alignLines(before,after),withRegionEndings(region,after.join('\\n'),'\\r\\n')];\n"
        "})));\n",
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
    for (before, after), (expected, endings) in zip(cases, json.loads(result.stdout), strict=True):
        assert TextEditor._align_lines(before, after) == expected, (before, after)
        region = "".join(
            line + ("\n" if index % 2 else "\r\n") if index < len(before) - 1 else line
            for index, line in enumerate(before)
        )
        assert TextEditor._region_endings(region, "\n".join(after), "\r\n") == endings, (
            before,
            after,
        )
