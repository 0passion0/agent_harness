"""Guard capability dependencies without prescribing private method decomposition."""

import ast
from pathlib import Path


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
