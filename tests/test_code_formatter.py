import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from vertical_harness.filesystem.code_formatter import CodeFormatter
from vertical_harness.config.formatter_entry import FormatterEntry


class TestCodeFormatter:
    def test_pinned_source_builtin_differential(self, tmp_path):
        reference = Path(os.environ.get("OPENCODE_REFERENCE", Path(__file__).resolve().parents[2] / "opencode"))
        source = reference / "packages/core/src/formatter/builtins.ts"
        effect = reference / "packages/core/node_modules/effect/dist/index.js"
        bun = shutil.which("bun")
        if not bun or not source.is_file() or not effect.is_file():
            pytest.skip("Pinned OpenCode checkout, its Effect dependency and Bun are required")
        assert subprocess.run(
            ["git", "-C", str(reference), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True,
        ).stdout.strip() == "7d3752c14ce8dd03cfaac03ee4062a877808a69c"
        harness = tmp_path / "formatter-source.ts"
        harness.write_text(r'''
import { pathToFileURL } from "node:url";
const {Effect} = await import(pathToFileURL(Bun.argv[3]).href);
const source = (await Bun.file(Bun.argv[2]).text()).split(/\r?\n/).filter(line => !line.startsWith('import ')).join('\n').replaceAll('export ', '');
const make = new Function('Effect', 'ChildProcess', 'which',
  new Bun.Transpiler({loader: 'ts'}).transformSync(source) + ';return make;')(
    Effect, {make: (...args) => args}, name => 'bin-' + name);
const entries = make({directory:'/project',worktree:'/project',bin:'/bin',
  fs:{findUp: target => Effect.succeed(['/project/'+target]),
      readJson: () => Effect.succeed({dependencies:{prettier:'1',oxfmt:'1'},require:{'laravel/pint':'1'}}),
      readFileString: () => Effect.succeed('[tool.ruff]')},
  npm:{which: name => Effect.succeed('npm-'+name)},
  processes:{run: () => Effect.succeed({exitCode:0,stdout:Buffer.from('R language formatter\n')})}});
const results = [];
for (const entry of entries) {
  const command = await Effect.runPromise(entry.enabled);
  if (command === false) throw new Error('Unexpected disabled builtin: ' + entry.name);
  results.push({name:entry.name,extensions:entry.extensions,arguments:command.slice(1),environment:entry.environment??{}});
}
console.log(JSON.stringify(results));
''', encoding="utf-8")
        expected = json.loads(subprocess.run(
            [bun, str(harness), str(source), str(effect)], check=True, capture_output=True,
            text=True, encoding="utf-8",
        ).stdout)
        formatter = CodeFormatter(tmp_path, True)
        assert [{"name": name, "extensions": list(entry["extensions"]),
                 "arguments": list(entry["arguments"]), "environment": entry["environment"]}
                for name, entry in formatter._entries.items()] == expected

    @staticmethod
    def command(suffix):
        return [sys.executable, "-c", "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_bytes(p.read_bytes()+sys.argv[2].encode())", "$FILE", suffix]

    @pytest.mark.parametrize(
        ("filename", "extension", "matches"),
        [("test.match", ".match", True), ("test.other", ".match", False),
         ("test.MATCH", ".match", False), ("test.MATCH", ".MATCH", True),
         (".match", ".match", False), (".match", "", True),
         ("README", ".match", False), ("README", "", True),
         ("test.part.match", ".match", True), ("test.part.match", ".part.match", False),
         ("test.", ".", True), ("..match", ".match", True)],
    )
    def test_source_extension_cases(self, tmp_path, filename, extension, matches):
        formatter = CodeFormatter(tmp_path, {"matching": {
            "command": [sys.executable, "-c", "pass", "$FILE"], "extensions": [extension],
        }})
        assert formatter.file(tmp_path / filename) is matches

    def test_source_order_and_failure_fallback(self, tmp_path):
        target = tmp_path / "test.seq"
        target.write_text("x")
        formatter = CodeFormatter(tmp_path, {
            "missing": {"command": [str(tmp_path / "missing"), "$FILE"], "extensions": [".seq"]},
            "failing": {"command": [sys.executable, "-c", "import sys;sys.exit(1)"], "extensions": [".seq"]},
            "first": {"command": self.command("A"), "extensions": [".seq"]},
            "second": {"command": self.command("B"), "extensions": [".seq"]},
        })
        assert formatter.file(target)
        assert target.read_text() == "xA"

    @pytest.mark.parametrize("configured", [None, False])
    def test_no_config_disables_all(self, tmp_path, configured):
        formatter = CodeFormatter(tmp_path, configured)
        with patch.object(formatter, "_which", return_value="formatter") as which:
            assert not formatter.file(tmp_path / "main.go")
        which.assert_not_called()

    def test_disabled_and_override_builtin(self, tmp_path):
        target = tmp_path / "main.go"
        target.write_text("")
        formatter = CodeFormatter(tmp_path, {"gofmt": {"disabled": True}})
        assert not formatter.file(target)
        formatter.reload({"gofmt": {"command": self.command("override")}})
        assert formatter.file(target)
        assert target.read_text() == "override"

    @pytest.mark.parametrize(
        ("layers", "expected"),
        [([{"a": {"extensions": [".a", ".b"]}}, {"a": {"extensions": [".b"]}}], {"a": {"extensions": [".b"], "environment": {}}}),
         ([{"a": {"disabled": True}}, {"a": {"command": ["x"]}}], {"a": {"disabled": True, "command": ["x"], "environment": {}}}),
         ([{"a": {"disabled": True}}, {"a": {"disabled": False}}], {"a": {"disabled": False, "environment": {}}}),
         ([{"a": {"environment": {"A": "a", "B": "b"}}}, None, {"a": {"environment": {"B": "c"}}}], {"a": {"environment": {"A": "a", "B": "c"}}}),
         ([{"a": {}}, {}], {"a": {"environment": {}}}),
         ([{"a": {}}, False], False),
         ([{"a": {}}, True, {"b": {}}], {"b": {"environment": {}}}),
         ([{"a": {}}, False, {"b": {}}], {"b": {"environment": {}}})],
    )
    def test_source_config_layers(self, layers, expected):
        assert CodeFormatter.resolve(layers) == expected

    def test_environment_and_argv_are_literal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORMATTER_INHERITED_SECRET", "hidden")
        target = tmp_path / "file ; $x.custom"
        formatter = CodeFormatter(tmp_path, {"custom": {
            "command": [sys.executable, "-c", "import os,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(os.getenv('FIRST','')+os.getenv('FORMATTER_INHERITED_SECRET',''))", "$FILE"],
            "extensions": [".custom"], "environment": {"FIRST": "configured"},
        }})
        assert formatter.file(target)
        assert target.read_text() == "configured"

    def test_command_cache_retries_disabled_and_reload(self, tmp_path):
        formatter = CodeFormatter(tmp_path, True)
        with patch.object(formatter, "_detect", side_effect=[None, ["old", "$FILE"], ["new", "$FILE"]]) as detect, patch.object(formatter, "_run", return_value=subprocess.CompletedProcess([], 0)) as run:
            assert not formatter.file(tmp_path / "a.go")
            assert formatter.file(tmp_path / "a.go")
            assert formatter.file(tmp_path / "a.go")
            assert detect.call_count == 2
            formatter.reload(True)
            assert formatter.file(tmp_path / "a.go")
            assert run.call_args.args[0][0] == "new"

    def test_all_26_builtin_commands(self, tmp_path):
        formatter = CodeFormatter(tmp_path, True)
        expected = {
            "gofmt": ["-w", "$FILE"], "mix": ["format", "$FILE"], "oxfmt": ["$FILE"],
            "prettier": ["--write", "$FILE"], "biome": ["format", "--write", "$FILE"],
            "zig": ["fmt", "$FILE"], "clang-format": ["-i", "$FILE"], "ktlint": ["-F", "$FILE"],
            "ruff": ["format", "$FILE"], "air": ["format", "$FILE"], "uv": ["format", "--", "$FILE"],
            "rubocop": ["--autocorrect", "$FILE"], "standardrb": ["--fix", "$FILE"],
            "htmlbeautifier": ["$FILE"], "dart": ["format", "$FILE"], "ocamlformat": ["-i", "$FILE"],
            "terraform": ["fmt", "$FILE"], "latexindent": ["-w", "-s", "$FILE"],
            "gleam": ["format", "$FILE"], "shfmt": ["-w", "$FILE"], "nixfmt": ["$FILE"],
            "rustfmt": ["$FILE"], "pint": ["$FILE"], "ormolu": ["-i", "$FILE"],
            "cljfmt": ["fix", "--quiet", "$FILE"], "dfmt": ["-i", "$FILE"],
        }
        assert list(formatter._entries) == list(expected)
        for name, arguments in expected.items():
            assert list(formatter._entries[name]["arguments"]) == arguments

    @pytest.mark.parametrize("name", ["prettier", "oxfmt"])
    def test_node_dependencies_and_detection_failures(self, tmp_path, name):
        formatter = CodeFormatter(tmp_path, True, npm_which=lambda package: "npm-" + package)
        arguments = formatter._entries[name]["arguments"]
        assert formatter._detect(name, arguments) is None
        (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {name: "1"}}))
        assert formatter._detect(name, arguments) == ["npm-" + name, *arguments]
        (tmp_path / "package.json").write_text("invalid")
        assert formatter._detect(name, arguments) is None

    @pytest.mark.parametrize("name,config", [("biome", "biome.jsonc"), ("clang-format", ".clang-format"), ("ocamlformat", ".ocamlformat")])
    def test_required_project_config(self, tmp_path, name, config):
        formatter = CodeFormatter(tmp_path, True, npm_which=lambda package: package)
        arguments = formatter._entries[name]["arguments"]
        with patch.object(formatter, "_which", return_value=name):
            assert formatter._detect(name, arguments) is None
            (tmp_path / config).write_text("")
            assert formatter._detect(name, arguments) is not None

    @pytest.mark.parametrize("config,content", [("pyproject.toml", "[tool.ruff]"), ("ruff.toml", ""), (".ruff.toml", ""), ("requirements.txt", "ruff==1"), ("Pipfile", "ruff")])
    def test_ruff_detection(self, tmp_path, config, content):
        formatter = CodeFormatter(tmp_path, True)
        with patch.object(formatter, "_which", return_value="ruff"):
            assert formatter._detect("ruff", ("format", "$FILE")) is None
            (tmp_path / config).write_text(content)
            assert formatter._detect("ruff", ("format", "$FILE")) == ["ruff", "format", "$FILE"]

    def test_ruff_uses_nearest_file_per_filename(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]")
        child = tmp_path / "child"
        child.mkdir()
        (child / "pyproject.toml").write_text("[tool.other]")
        formatter = CodeFormatter(tmp_path, True, directory=child)
        with patch.object(formatter, "_which", return_value="ruff"):
            assert formatter._detect("ruff", ("format", "$FILE")) is None

    @pytest.mark.parametrize("name,stdout,code,enabled", [("air", b"R language formatter\n", 0, True), ("air", b"wrong\nR language formatter", 0, False), ("air", b"R language formatter", 1, False), ("uv", b"", 0, True), ("uv", b"", 1, False)])
    def test_air_and_uv_help_probes(self, tmp_path, name, stdout, code, enabled):
        formatter = CodeFormatter(tmp_path, True)
        with patch.object(formatter, "_which", return_value=name), patch.object(formatter, "_run", return_value=subprocess.CompletedProcess([], code, stdout)) as run:
            assert (formatter._detect(name, formatter._entries[name]["arguments"]) is not None) is enabled
            assert run.call_args.args[0] == ([name, "--help"] if name == "air" else [name, "format", "--help"])

    def test_pint_and_find_up_stop_at_workspace(self, tmp_path):
        child = tmp_path / "child"
        child.mkdir()
        (tmp_path / "composer.json").write_text('{"require-dev":{"laravel/pint":"1"}}')
        formatter = CodeFormatter(tmp_path, True, directory=child)
        assert formatter._detect("pint", ("$FILE",)) == ["./vendor/bin/pint", "$FILE"]
        assert formatter._find_up("composer.json") == [tmp_path / "composer.json"]

    def test_accepted_model_config_and_invalid_fields(self, tmp_path):
        formatter = CodeFormatter(tmp_path, {"custom": FormatterEntry(command=["x"], extensions=[".x"])})
        assert "custom" in formatter._entries
        for entry in ({"disabled": "yes"}, {"command": "x"}, {"environment": {"A": 1}}, {"extensions": None}):
            with pytest.raises(ValidationError):
                formatter.reload({"custom": entry})
        with pytest.raises(ValidationError):
            formatter.reload("yes")

    def test_target_and_workdir_must_stay_inside_workspace(self, tmp_path):
        formatter = CodeFormatter(tmp_path, False)
        with pytest.raises(ToolException):
            formatter.file(tmp_path.parent / "outside.py")
        with pytest.raises(ToolException):
            CodeFormatter(tmp_path, directory=tmp_path.parent)

    def test_async_api_waits_for_cancellation_settlement(self, tmp_path):
        target = tmp_path / "async.custom"
        ready = tmp_path / "ready"
        command = [sys.executable, "-c", "import time,pathlib,sys; pathlib.Path('ready').write_text('started'); time.sleep(30);pathlib.Path(sys.argv[1]).write_text('done')", "$FILE"]
        formatter = CodeFormatter(tmp_path, {"custom": {"command": command, "extensions": [".custom"]}})
        async def run():
            task = asyncio.create_task(formatter.afile(target))
            async with asyncio.timeout(10):
                while not ready.exists():
                    await asyncio.sleep(.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not target.exists()
        asyncio.run(run())
