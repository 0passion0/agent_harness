"""Guard the backend class structure and per-session state boundaries."""

import ast
from pathlib import Path

from vertical_harness.desktop import AgentLoop
from vertical_harness.tool_details import ToolDetails


def test_backend_logic_is_in_methods_with_one_class_per_module():
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / "packages/agent-harness/src/vertical_harness").rglob("*.py"))
    paths += sorted((root / "scripts").glob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        assert len(classes) <= 1, path
        functions = (ast.FunctionDef, ast.AsyncFunctionDef)
        assert not any(isinstance(node, functions) for node in tree.body), path
        for cls in classes:
            for method in cls.body:
                if isinstance(method, functions):
                    assert not any(
                        isinstance(node, (*functions, ast.ClassDef))
                        for statement in method.body
                        for node in ast.walk(statement)
                    ), (path, method.name)


def test_sessions_and_callbacks_keep_independent_mutable_state():
    first, second = AgentLoop(), AgentLoop()
    assert first.active is not second.active
    assert first.output_lock is not second.output_lock
    left, right = ToolDetails(), ToolDetails()
    for name in ("records", "started", "context_requests", "context_reports"):
        assert getattr(left, name) is not getattr(right, name)
