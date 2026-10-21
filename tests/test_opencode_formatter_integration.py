import asyncio
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from test_opencode_harness_workflows import WorkflowModel, call
from vertical_harness.config import HarnessSettings
from vertical_harness.core import compose
from vertical_harness.core.context import RunContext
from vertical_harness.storage.document_backend import DocumentFilesystemBackend
from vertical_harness.tools.opencode_formatter import OpenCodeFormatter


def formatter_config(exit_code=0):
    return {"report": {
        "extensions": [".txt"],
        "command": [sys.executable, "-c",
                    "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
                    "p.write_bytes(p.read_bytes().removeprefix(bytes.fromhex('efbbbf')).upper()); "
                    "sys.exit(int(sys.argv[2]))", "$FILE", str(exit_code)],
    }}


@pytest.mark.parametrize("operation", ["write", "edit", "patch"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_mutations_preserve_bom_only_after_successful_formatter(tmp_path, operation, exit_code):
    target = tmp_path / "report.txt"
    target.write_bytes(b"\xef\xbb\xbfold\n")
    backend = DocumentFilesystemBackend(
        root_dir=tmp_path, virtual_mode=True,
        formatter=OpenCodeFormatter(tmp_path, formatter_config(exit_code)),
    )
    if operation == "write":
        assert backend.write("/report.txt", "new\n").error is None
    elif operation == "edit":
        assert backend.edit("/report.txt", "old", "new").error is None
    else:
        _, artifact = backend.apply_patch(
            "*** Begin Patch\n*** Update File: report.txt\n@@\n-old\n+new\n*** End Patch",
            lambda _: None,
        )
        # Source patch reports its pre-format fallback when every command fails.
        assert ("+NEW\n" if exit_code == 0 else "+new\n") in artifact["files"][0]["patch"]
    assert target.read_bytes() == (b"\xef\xbb\xbf" if exit_code == 0 else b"") + b"NEW\n"


def test_patch_formats_unique_destinations_after_all_writes(tmp_path):
    (tmp_path / "a.txt").write_text("first\n")
    config = {"report": {
        "extensions": [".txt"],
        "command": [sys.executable, "-c",
                    "from pathlib import Path; import sys; "
                    "assert Path('a.txt').exists() and Path('b.txt').exists(); "
                    "p=Path(sys.argv[1]); p.write_bytes(p.read_bytes().upper()); "
                    "p.with_suffix('.count').open('a').write('x')", "$FILE"],
    }}
    backend = DocumentFilesystemBackend(
        root_dir=tmp_path, virtual_mode=True, formatter=OpenCodeFormatter(tmp_path, config),
    )
    _, artifact = backend.apply_patch(
        "*** Begin Patch\n*** Update File: a.txt\n@@\n-first\n+middle\n*** Update File: a.txt\n@@\n-middle\n+second\n*** Add File: b.txt\n+other\n*** End Patch",
        lambda _: None,
    )
    assert (tmp_path / "a.txt").read_bytes() == b"SECOND\n"
    assert (tmp_path / "b.txt").read_bytes() == b"OTHER\n"
    assert (tmp_path / "a.count").read_text() == "x"
    assert (tmp_path / "b.count").read_text() == "x"
    assert "+SECOND\n" in artifact["files"][1]["patch"]


@pytest.mark.parametrize("execution", ["sync", "async"])
def test_formatter_runs_in_native_general_purpose_agent(tmp_path, monkeypatch, execution):
    model = WorkflowModel(responses=[
        call("write_file", {"file_path": "/report.txt", "content": "total: 42\n"}, "write"),
        call("read_file", {"file_path": "/report.txt"}, "read"),
        AIMessage(content="Report saved and verified."),
    ])
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    settings = HarnessSettings(
        _env_file=None, model="test:model", workspace_root=tmp_path, store_backend="memory",
        enable_planning=False, formatter=formatter_config(),
    )
    agent = compose.AgentComposer.build_agent(settings, checkpointer=InMemorySaver())
    request = {"messages": [HumanMessage(content="Save the total in a report and read it back.")]}
    config = {"configurable": {"thread_id": execution}}
    context = RunContext("tenant", "user")
    result = (agent.invoke(request, config, context=context) if execution == "sync" else
              asyncio.run(agent.ainvoke(request, config, context=context)))
    assert (tmp_path / "report.txt").read_bytes() == b"TOTAL: 42\n"
    read = next(item for item in result["messages"]
                if isinstance(item, ToolMessage) and item.name == "read_file")
    assert "TOTAL: 42" in read.content


def test_formatter_config_rejects_invalid_shape_before_writing(tmp_path):
    with pytest.raises(ValidationError):
        HarnessSettings(_env_file=None, workspace_root=tmp_path, formatter={
            "bad": {"command": "arbitrary shell string", "extensions": [".txt"]},
        })
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bom", [False, True])
def test_successful_formatter_canonicalizes_all_leading_boms(tmp_path, bom):
    target = tmp_path / "report.txt"
    target.write_bytes((b"\xef\xbb\xbf" if bom else b"") + b"old")
    config = {"report": {
        "extensions": [".txt"],
        "command": [sys.executable, "-c",
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[1]).write_bytes(bytes.fromhex('efbbbfefbbbf')+b'new')", "$FILE"],
    }}
    backend = DocumentFilesystemBackend(
        root_dir=tmp_path, virtual_mode=True, formatter=OpenCodeFormatter(tmp_path, config),
    )
    assert backend.write("/report.txt", "new").error is None
    assert target.read_bytes() == (b"\xef\xbb\xbf" if bom else b"") + b"new"
