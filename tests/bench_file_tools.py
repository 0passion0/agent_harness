"""Run with .venv/Scripts/python.exe tests/bench_opencode_file_tools.py.

Requires installed Bun and an OpenCode checkout; no dependencies are installed.
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path

from vertical_harness.tools.patch_editor import PatchEditor
from vertical_harness.tools.text_editor import TextEditor
from vertical_harness.tools.update_chunk import UpdateChunk

SOURCE_COMMIT = "7d3752c14ce8dd03cfaac03ee4062a877808a69c"

_SOURCE_BENCHMARK = r"""
const root = process.argv[2];
const edit = await Bun.file(root + '/packages/core/src/tool/plugin/edit.ts').text();
const patch = await Bun.file(root + '/packages/util/src/patch.ts').text();
const bom = await Bun.file(root + '/packages/util/src/bom.ts').text();
const transpiler = new Bun.Transpiler({loader: 'ts'});
const helpers = edit.slice(edit.indexOf('const crlf ='), edit.indexOf('/** Deferred edit'));
const editFn = new Function(transpiler.transformSync(helpers) + `
return (source, old, next) => {
  const {matches, replace} = matchView(source, old, next);
  if (matches.length !== 1) throw Error('Expected exactly one match');
  return matches.toReversed().reduce((s, m) => s.slice(0, m.start) + replace(m) + s.slice(m.end), source);
}`)();
const bomHelpers = bom.slice(bom.indexOf('const code ='), bom.indexOf('export const readFile'))
  .replaceAll('export function ', 'function ');
const patchHelpers = patch.slice(patch.indexOf('export function parse('))
  .replaceAll('export function ', 'function ');
const patchFn = new Function(transpiler.transformSync(bomHelpers + '\nconst Bom = {split,join};\n' + patchHelpers)
  + ';return (path,chunks,text) => { const result=derive(path,chunks,text);return Bom.join(result.content,result.bom) };')();
for (const c of JSON.parse(await Bun.stdin.text())) {
  const chunks = c.chunks?.map(chunk => ({oldLines: chunk.old_lines, newLines: chunk.new_lines,
    changeContext: chunk.change_context, endOfFile: chunk.end_of_file}));
  const run = c.operation === 'edit' ? () => editFn(c.content,c.old,c.new)
    : () => patchFn('file',chunks,c.content);
  if (run() !== c.expected) throw Error('Source and Python output differ: ' + c.case);
  const started = performance.now();
  for (let i=0; i<c.repeats; i++) run();
  console.log(JSON.stringify({implementation:'source_bun',case:c.case,
    milliseconds:(performance.now()-started)/c.repeats}));
}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path(
            os.environ.get("OPENCODE_REFERENCE", Path(__file__).resolve().parents[2] / "opencode")
        ),
    )
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--large-repeats", type=int, default=1)
    args = parser.parse_args()
    if args.repeats < 1 or args.large_repeats < 1:
        parser.error("repeat counts must be positive")
    bun = shutil.which("bun")
    if bun is None:
        parser.error("Bun must already be installed")
    commit = subprocess.run(
        ["git", "-C", str(args.reference), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    if commit != SOURCE_COMMIT:
        parser.error(f"reference must be at {SOURCE_COMMIT}, found {commit}")
    content = "".join(f"line {i}\r\n" for i in range(10000))
    old = "\n".join(f"line {i}" for i in range(4999, 5002))
    large = "\n".join(f"line {i}" for i in range(1000))
    chunk = UpdateChunk(
        ("line 4999", "line 5000", "line 5001"),
        ("line 4999", "updated 5000", "line 5001"),
    )
    cases = [
        {
            "case": "edit_10k_local",
            "operation": "edit",
            "content": content,
            "old": old,
            "new": old.replace("line 5000", "updated 5000"),
            "repeats": args.repeats,
        },
        {
            "case": "patch_10k_local",
            "operation": "patch",
            "content": content,
            "chunks": [asdict(chunk)],
            "repeats": args.repeats,
        },
        {
            "case": "edit_1k_region",
            "operation": "edit",
            "content": content,
            "old": large,
            "new": large.replace("line 500\n", "updated 500\n"),
            "repeats": args.large_repeats,
        },
    ]
    print(
        json.dumps(
            {
                "source_commit": commit,
                "file_lines": 10000,
                "file_characters": len(content),
                "memory_measurement": "Python tracemalloc peak bytes; timing excludes tracing",
            }
        )
    )
    for case in cases:
        if case["operation"] == "edit":

            def run(case=case):
                return TextEditor.replace_text(case["content"], case["old"], case["new"])
        else:

            def run(case=case):
                return PatchEditor.derive_update("file", (chunk,), case["content"])

        case["expected"] = run()
        started = time.perf_counter()
        for _ in range(case["repeats"]):
            run()
        elapsed = (time.perf_counter() - started) * 1000 / case["repeats"]
        tracemalloc.start()
        run()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(
            json.dumps(
                {
                    "implementation": "python",
                    "case": case["case"],
                    "milliseconds": elapsed,
                    "peak_traced_bytes": peak,
                }
            )
        )
    with tempfile.TemporaryDirectory(prefix="opencode-bench-") as directory:
        script = Path(directory) / "bench.ts"
        script.write_text(_SOURCE_BENCHMARK, encoding="utf-8")
        result = subprocess.run(
            [bun, str(script), str(args.reference.resolve())],
            input=json.dumps(cases),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        print(result.stdout, end="")


if __name__ == "__main__":
    main()
