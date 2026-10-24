"""Guard capability dependencies without prescribing private method decomposition."""

import ast
import os
import subprocess
import sys
from graphlib import TopologicalSorter
from pathlib import Path

import pytest

from vertical_harness import models


def test_pydantic_models_are_centralized_and_exported_without_aliases():
    root = Path(__file__).resolve().parents[1] / "packages/agent-harness/src/vertical_harness"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module in {"pydantic", "pydantic_settings"}
                and any(alias.name in {"BaseModel", "BaseSettings"} for alias in node.names)
            ):
                assert path.parent.name == "models", path
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("vertical_harness.models")
                and path.parent.name != "models"
            ):
                assert node.module == "vertical_harness.models", path
                assert all(alias.asname is None for alias in node.names), path
    for name in models.__all__:
        model = getattr(models, name)
        if isinstance(model, type):
            assert model.__module__.startswith("vertical_harness.models.")


@pytest.mark.parametrize("module", ["models", "core.context", "storage.store"])
def test_leaf_import_does_not_load_agent_or_desktop(module, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import vertical_harness.{module}; import sys; "
            "assert 'vertical_harness.desktop' not in sys.modules; "
            "assert 'vertical_harness.core.compose' not in sys.modules",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_desktop_module_starts_without_duplicate_import_warning(tmp_path):
    result = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "vertical_harness.desktop"],
        cwd=tmp_path,
        env={**os.environ, "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"},
        input="",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_backend_concrete_module_imports_are_acyclic():
    root = Path(__file__).resolve().parents[1] / "packages/agent-harness/src"
    modules = {}
    for path in (root / "vertical_harness").rglob("*.py"):
        name = ".".join(path.relative_to(root).with_suffix("").parts)
        modules[name] = path
    dependencies = {}
    for name, path in modules.items():
        dependencies[name] = {
            node.module
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom) and node.module in modules
        }
    assert len(tuple(TopologicalSorter(dependencies).static_order())) == len(modules)


def test_backend_capabilities_do_not_import_host_layers():
    root = Path(__file__).resolve().parents[1] / "packages/agent-harness/src/vertical_harness"
    forbidden = {
        "config": {
            "bridge",
            "runtime",
            "desktop",
            "core",
            "execution",
            "filesystem",
            "storage",
            "tools",
            "middleware",
            "langchain",
        },
        "storage": {
            "bridge",
            "runtime",
            "desktop",
            "filesystem",
            "execution",
            "tools",
            "middleware",
            "core",
        },
        "execution": {"bridge", "runtime", "desktop", "filesystem", "storage", "middleware"},
        "filesystem": {"bridge", "runtime", "desktop", "storage", "tools", "middleware", "core"},
        "runtime": {"bridge", "desktop"},
        "middleware": {"bridge", "runtime", "desktop"},
        "langchain": {
            "bridge",
            "runtime",
            "desktop",
            "filesystem",
            "storage",
            "tools",
            "middleware",
        },
    }
    for path in root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        layer = path.relative_to(root).parts[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            for module in modules:
                if not module.startswith("vertical_harness"):
                    continue
                assert module != "vertical_harness", (path, node.lineno, "import the owning module")
                target = module.split(".")[1]
                assert target not in forbidden.get(layer, set()), (path, node.lineno, module)
                if layer in {"execution", "langchain"} and target == "core":
                    assert module == "vertical_harness.core.context", (path, node.lineno, module)
