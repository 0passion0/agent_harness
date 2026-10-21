import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from vertical_harness.middleware.project_instructions import ProjectInstructionsMiddleware
from vertical_harness.storage.document_backend import DocumentFilesystemBackend


def request(system_message=None):
    return ModelRequest(
        model=Mock(),
        messages=[HumanMessage(content="current user request")],
        system_message=system_message,
    )


def test_root_instructions_preserve_system_metadata_and_current_messages(tmp_path):
    content = "Follow the project conventions.\n" * 2100
    (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    )
    system = SystemMessage(
        content=[{"type": "text", "text": "base", "cache_control": {"type": "ephemeral"}}],
        name="original",
        id="system-id",
        additional_kwargs={"custom": "retained"},
        response_metadata={"provider": "retained"},
    )
    original = request(system)
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    prepared = handler.call_args.args[0]
    assert prepared.messages is original.messages
    assert prepared.system_message.model_dump(exclude={"content"}) == system.model_dump(
        exclude={"content"}
    )
    assert prepared.system_message.content[0] == system.content[0]
    assert prepared.system_message.content[-1]["text"] == (
        f"Instructions from: {tmp_path / 'AGENTS.md'}\n{content}"
    )
    assert original.system_message is system
    assert len(system.content) == 1


def test_updates_and_removal_are_reflected_without_mutating_request(tmp_path):
    instructions = tmp_path / "AGENTS.md"
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    )
    original = request()
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0] is original
    for content in ["first instructions", "updated instructions"]:
        instructions.write_text(content, encoding="utf-8")
        middleware.wrap_model_call(original, handler)
        system = handler.call_args.args[0].system_message
        assert system.content[-1]["text"].endswith(content)
    instructions.unlink()
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0] is original


def test_read_deny_prevents_instruction_access(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("secret instructions", encoding="utf-8")
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=[FilesystemPermission(operations=["read"], paths=["/AGENTS.md"], mode="deny")],
    )
    read = Mock(side_effect=AssertionError("denied file must not be opened"))
    monkeypatch.setattr(Path, "read_text", read)
    original = request()
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0] is original
    read.assert_not_called()


def test_async_reads_off_event_loop_and_preserves_handler_result(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("async instructions", encoding="utf-8")
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    )
    event_loop_thread = threading.get_ident()
    read_text = Path.read_text
    reader_threads = []

    def read(path, **kwargs):
        reader_threads.append(threading.get_ident())
        return read_text(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    handler = AsyncMock(return_value="response")
    original = request()
    assert asyncio.run(middleware.awrap_model_call(original, handler)) == "response"
    assert reader_threads and reader_threads[0] != event_loop_thread
    assert handler.call_args.args[0].system_message.content[-1]["text"].endswith(
        "async instructions"
    )
    assert handler.call_args.args[0].messages is original.messages


def test_permission_error_skips_but_unexpected_read_errors_propagate(tmp_path, monkeypatch):
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    )
    original = request()
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=PermissionError("denied")))
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0] is original
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=OSError("unexpected")))
    with pytest.raises(OSError, match="unexpected"):
        middleware.wrap_model_call(original, handler)
    (tmp_path / "AGENTS.md").write_bytes(b"\xff")
    monkeypatch.undo()
    with pytest.raises(UnicodeDecodeError):
        middleware.wrap_model_call(original, handler)


def test_only_workspace_root_instructions_are_loaded(tmp_path):
    (tmp_path / "AGENTS.md").write_text("ancestor instructions", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nested = workspace / "nested"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("nested instructions", encoding="utf-8")
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=workspace, virtual_mode=True)
    )
    original = request()
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0] is original


def test_nearby_instructions_refresh_without_mutating_history(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    path = nested / "AGENTS.md"
    path.write_text("updated guidance", encoding="utf-8-sig")
    instructions = HumanMessage(
        content=f"Instructions from: {path}\nold guidance",
        id="guidance-id",
        additional_kwargs={"opencode_instructions": [str(path)], "custom": "retained"},
        response_metadata={"provider": "retained"},
    )
    original = request().override(messages=[instructions, HumanMessage(content="continue")])
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    )
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    prepared = handler.call_args.args[0]
    assert prepared.messages[0].content == f"Instructions from: {path}\nupdated guidance"
    assert prepared.messages[0].model_dump(exclude={"content"}) == instructions.model_dump(
        exclude={"content"}
    )
    assert prepared.messages[1] is original.messages[1]
    assert original.messages[0].content.endswith("old guidance")
    path.unlink()
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0].messages == [original.messages[1]]
    path.write_text("restored guidance", encoding="utf-8")
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0].messages[0].content.endswith("restored guidance")


def test_nearby_instructions_remove_only_files_no_longer_available(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    allowed = nested / "AGENTS.md"
    allowed.write_text("current guidance", encoding="utf-8")
    denied = nested / "private.md"
    denied.write_text("private guidance", encoding="utf-8")
    empty = nested / "empty.md"
    empty.touch()
    paths = [str(allowed), str(denied), str(empty), str(nested / "removed.md")]
    instructions = HumanMessage(
        content="old guidance", additional_kwargs={"opencode_instructions": paths}
    )
    original = request().override(messages=[instructions])
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=[
            FilesystemPermission(operations=["read"], paths=["/nested/private.md"], mode="deny")
        ],
    )
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    refreshed = handler.call_args.args[0].messages[0]
    assert refreshed.content == f"Instructions from: {allowed}\ncurrent guidance"
    assert refreshed.additional_kwargs["opencode_instructions"] == [str(allowed)]
    assert instructions.additional_kwargs["opencode_instructions"] == paths


def test_nearby_instructions_outside_current_workspace_are_not_read(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "AGENTS.md"
    outside.write_text("outside guidance", encoding="utf-8")
    instructions = HumanMessage(
        content="old guidance", additional_kwargs={"opencode_instructions": [str(outside)]}
    )
    original = request().override(messages=[instructions, HumanMessage(content="continue")])
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=workspace, virtual_mode=True)
    )
    read = Mock(side_effect=FileNotFoundError)
    monkeypatch.setattr(Path, "read_text", read)
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0].messages == [original.messages[1]]
    read.assert_called_once_with(encoding="utf-8")


def test_unchanged_nearby_instructions_preserve_request_identity(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    path = nested / "AGENTS.md"
    path.write_text("current guidance", encoding="utf-8")
    instructions = HumanMessage(
        content=f"Instructions from: {path}\ncurrent guidance",
        additional_kwargs={"opencode_instructions": [str(path)]},
    )
    original = request().override(messages=[instructions])
    middleware = ProjectInstructionsMiddleware(
        DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    )
    handler = Mock()
    middleware.wrap_model_call(original, handler)
    assert handler.call_args.args[0] is original
