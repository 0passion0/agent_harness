import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vertical_harness.core import compose
from vertical_harness.core.compose import AgentComposer
from vertical_harness.filesystem.document_backend import DocumentFilesystemBackend
from vertical_harness.filesystem.file_mutation import FileMutation
from vertical_harness.models import HarnessSettings


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def build_agent(tmp_path, monkeypatch, calls, mode="full"):
    model = ToolModel(
        responses=[
            *[
                AIMessage(content="", tool_calls=[{"id": str(i), "name": name, "args": args}])
                for i, (name, args) in enumerate(calls)
            ],
            AIMessage(content="Done"),
        ]
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    settings = HarnessSettings(
        _env_file=None,
        model="test:files",
        workspace_root=tmp_path,
        store_backend="memory",
    )
    return AgentComposer.build_agent(
        HarnessSettings.model_validate({**(settings).model_dump(), **{"permission_mode": mode}}),
        checkpointer=InMemorySaver(),
    )


@pytest.mark.parametrize("execution", ["sync", "async", "stream"])
def test_all_file_tools_through_existing_graph(tmp_path, monkeypatch, execution):
    patch = "*** Begin Patch\n*** Update File: x.txt\n*** Move to: moved.txt\n@@\n alpha!\n-beta\n+GAMMA\n*** End Patch"
    calls = [
        ("write_file", {"file_path": "/x.txt", "content": "\ufeffalpha\r\nbeta\r\n"}),
        ("edit_file", {"file_path": "/x.txt", "old_string": "alpha", "new_string": "alpha!"}),
        ("apply_patch", {"patch_text": patch}),
        ("read_file", {"file_path": "/moved.txt", "offset": 1, "limit": 1}),
        ("glob", {"pattern": "*.txt"}),
        (
            "grep",
            {
                "pattern": "g.mma",
                "literal": False,
                "case_sensitive": False,
                "output_mode": "content",
            },
        ),
    ]
    agent = build_agent(tmp_path, monkeypatch, calls)
    config = {"configurable": {"thread_id": execution}}
    inputs = {"messages": [HumanMessage(content="Change and inspect the files")]}
    if execution == "sync":
        result = agent.invoke(inputs, config)
    else:

        async def run():
            if execution == "async":
                return await agent.ainvoke(inputs, config)
            values = [value async for value in agent.astream(inputs, config, stream_mode="values")]
            return values[-1]

        result = asyncio.run(run())
    messages = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}
    assert len(messages) == 6
    assert all(message.status == "success" for message in messages.values()), messages
    assert "GAMMA" in messages["3"].content
    assert "/moved.txt" in messages["4"].content
    assert "GAMMA" in messages["5"].content
    detail = messages["5"].artifact["matches"][0]
    assert detail["entry"]["path"] == "/moved.txt"
    assert detail["offset"] == 7  # ripgrep offsets refer to decoded input after the UTF-8 BOM.
    assert detail["submatches"] == [{"text": "GAMMA", "start": 0, "end": 5}]
    # Source patch rewrites context within a hunk with LF as well.
    assert (
        next(item for item in messages["2"].artifact["files"] if item["file"] == "moved.txt")[
            "additions"
        ]
        == 2
    )
    assert not (tmp_path / "x.txt").exists()
    assert (tmp_path / "moved.txt").read_bytes() == b"\xef\xbb\xbfalpha!\nGAMMA\n"


@pytest.mark.parametrize(
    "mode,decision", [("read_only", None), ("ask", "reject"), ("ask", "approve")]
)
def test_patch_uses_existing_permissions_and_checkpoint_resume(
    tmp_path, monkeypatch, mode, decision
):
    patch = "*** Begin Patch\n*** Add File: new.txt\n+created\n*** End Patch"
    agent = build_agent(tmp_path, monkeypatch, [("apply_patch", {"patch_text": patch})], mode)
    config = {"configurable": {"thread_id": "permissions"}}
    if mode == "read_only":
        with pytest.raises(PermissionError, match="apply_patch is disabled"):
            agent.invoke({"messages": [HumanMessage(content="Create file")]}, config)
        assert not (tmp_path / "new.txt").exists()
        return
    result = agent.invoke({"messages": [HumanMessage(content="Create file")]}, config)
    if mode == "ask":
        assert not (tmp_path / "new.txt").exists()
        assert result["__interrupt__"][0].value["name"] == "apply_patch"
        result = agent.invoke(Command(resume={"type": decision}), config)
    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == ("success" if decision == "approve" else "error")
    assert (tmp_path / "new.txt").exists() == (decision == "approve")


@pytest.mark.parametrize(
    "last",
    [
        "*** Update File: absent.txt\n@@\n-missing\n+new",
        "*** Add File: ../outside.txt\n+escape",
        "*** Add File: bad.txt\n+\ud800",
    ],
)
def test_patch_preflights_every_hunk_and_encoding_before_writes(tmp_path, last):
    backend = DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    patch = "*** Begin Patch\n*** Add File: first.txt\n+first\n" + last + "\n*** End Patch"
    with pytest.raises((OSError, ValueError)):
        backend.apply_patch(patch, lambda _: None)
    assert list(tmp_path.iterdir()) == []


def test_patch_move_permissions_are_checked_before_any_writes(tmp_path, monkeypatch):
    (tmp_path / "x.txt").write_bytes(b"old\n")
    patch = "*** Begin Patch\n*** Add File: first.txt\n+first\n*** Update File: x.txt\n*** Move to: .env\n@@\n-old\n+secret\n*** End Patch"
    agent = build_agent(tmp_path, monkeypatch, [("apply_patch", {"patch_text": patch})])
    result = agent.invoke(
        {"messages": [HumanMessage(content="Apply patch")]},
        {"configurable": {"thread_id": "private"}},
    )
    assert next(m for m in result["messages"] if isinstance(m, ToolMessage)).status == "error"
    assert (tmp_path / "x.txt").read_bytes() == b"old\n"
    assert not (tmp_path / "first.txt").exists()
    assert not (tmp_path / ".env").exists()


def test_partial_commit_reports_completed_files(tmp_path, monkeypatch):
    backend = DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    original = FileMutation.atomic_write

    def fail_second(path, data):
        if path.name == "second.txt":
            raise PermissionError("locked by another application")
        original(path, data)

    monkeypatch.setattr(FileMutation, "atomic_write", fail_second)
    patch = "*** Begin Patch\n*** Add File: first.txt\n+first\n*** Add File: second.txt\n+second\n*** End Patch"
    with pytest.raises(OSError, match=r"Completed before failure: first\.txt"):
        backend.apply_patch(patch, lambda _: None)
    assert (tmp_path / "first.txt").read_bytes() == b"first\n"
    assert not (tmp_path / "second.txt").exists()


def test_cross_backend_mutations_share_file_locks(tmp_path, monkeypatch):
    (tmp_path / "x.txt").write_bytes(b"\xef\xbb\xbffirst\r\nsecond\r\n")
    backends = [DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True) for _ in range(2)]
    write = FileMutation.atomic_write
    started = threading.Event()

    def slow_write(path, data):
        started.set()
        time.sleep(0.05)
        write(path, data)

    monkeypatch.setattr(FileMutation, "atomic_write", slow_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(backends[0].edit, "/x.txt", "first", "one")
        assert started.wait(2)
        second = executor.submit(backends[1].edit, "/x.txt", "second", "two")
        assert first.result().error is None
        assert second.result().error is None
    assert (tmp_path / "x.txt").read_bytes() == b"\xef\xbb\xbfone\r\ntwo\r\n"


@pytest.mark.parametrize("operation", ["write", "patch"])
def test_async_cancel_waits_for_worker_and_stops_pending_commit(tmp_path, monkeypatch, operation):
    backend = DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    started, release = threading.Event(), threading.Event()
    original = FileMutation.atomic_write

    def blocked_write(path, data):
        if path.name == "second.txt":
            started.set()
            assert release.wait(3)
        original(path, data)

    monkeypatch.setattr(FileMutation, "atomic_write", blocked_write)

    async def run():
        if operation == "write":
            task = asyncio.create_task(backend.awrite("/second.txt", "second"))
        else:
            patch = "*** Begin Patch\n*** Add File: first.txt\n+first\n*** Add File: second.txt\n+second\n*** End Patch"
            task = asyncio.create_task(backend.aapply_patch(patch, lambda _: None))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not (tmp_path / "second.txt").exists()
    assert (tmp_path / "first.txt").exists() == (operation == "patch")
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("execution", ["sync", "async"])
def test_directory_recovery_and_durable_nearby_instructions(tmp_path, monkeypatch, execution):
    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "z-dir").mkdir()
    actual = folder / "owner’s.txt"
    actual.write_bytes(b"read me\n")
    (folder / ".env").write_bytes(b"private")
    (folder / "AGENTS.md").write_bytes(b"Only modify this directory.")
    (tmp_path / "AGENTS.md").write_bytes(b"Root already supplied by initial instructions.")
    calls = [
        ("read_file", {"file_path": "/src/owner's.txt"}),
        ("read_file", {"file_path": "/src", "offset": 0, "limit": 1}),
        ("read_file", {"file_path": "/src/owner’s.txt"}),
    ]
    agent = build_agent(tmp_path, monkeypatch, calls)
    config = {"configurable": {"thread_id": "instructions"}}
    inputs = {"messages": [HumanMessage(content="Read source files")]}
    result = (
        agent.invoke(inputs, config)
        if execution == "sync"
        else asyncio.run(agent.ainvoke(inputs, config))
    )
    tools = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert all(m.status == "success" for m in tools)
    assert "read me" in tools[0].content
    assert "z-dir/" in tools[1].content
    assert "offset: 1" in tools[1].content
    assert ".env" not in tools[1].content
    instructions = [
        m
        for m in agent.get_state(config).values["messages"]
        if isinstance(m, HumanMessage) and "harness_instructions" in m.additional_kwargs
    ]
    assert len(instructions) == 1
    assert instructions[0].additional_kwargs["harness_instructions"] == [str(folder / "AGENTS.md")]
    assert "Only modify this directory." in instructions[0].content
    assert "Root already" not in instructions[0].content


@pytest.mark.parametrize(
    "name,content,args,mime",
    [
        ("no_extension", b"\x89PNG\r\n\x1a\n", {}, "image/png"),
        ("document.pdf", b"%PDF-1.7", {"native_media": True}, "application/pdf"),
    ],
)
def test_signature_media_through_graph(tmp_path, monkeypatch, name, content, args, mime):
    (tmp_path / name).write_bytes(content)
    agent = build_agent(tmp_path, monkeypatch, [("read_file", {"file_path": "/" + name, **args})])
    result = agent.invoke(
        {"messages": [HumanMessage(content="Inspect media")]},
        {"configurable": {"thread_id": "media"}},
    )
    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == "success"
    assert message.additional_kwargs["read_file_media_type"] == mime
    assert message.content_blocks[0]["mime_type"] == mime


def test_missing_read_suggestions_and_grep_artifact_obey_permissions(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("needle", encoding="utf-8")
    (tmp_path / "env-notes.txt").write_text("needle", encoding="utf-8")
    calls = [("read_file", {"file_path": "/env"}), ("grep", {"pattern": "needle"})]
    agent = build_agent(tmp_path, monkeypatch, calls)
    result = agent.invoke(
        {"messages": [HumanMessage(content="Find files")]},
        {"configurable": {"thread_id": "filtered-metadata"}},
    )
    read, grep = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert read.status == "error"
    assert "env-notes.txt" in read.content and ".env" not in read.content
    assert [m["entry"]["path"] for m in grep.artifact["matches"]] == ["/env-notes.txt"]


def test_patch_delete_overwrite_and_repeated_update_preflight(tmp_path):
    backend = DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    (tmp_path / "old.txt").write_bytes(b"old")
    (tmp_path / "overwrite.txt").write_bytes(b"previous")
    (tmp_path / "update.txt").write_bytes(b"first")
    patch = """*** Begin Patch
*** Delete File: old.txt
*** Add File: overwrite.txt
+new
*** Update File: update.txt
@@
-first
+second
*** Update File: update.txt
@@
-second
+third
*** End Patch"""
    _, metadata = backend.apply_patch(patch, lambda _: None)
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "overwrite.txt").read_bytes() == b"new\n"
    assert (tmp_path / "update.txt").read_bytes() == b"third\n"
    assert "\\ No newline at end of file" in metadata["files"][0]["patch"]
    assert len(metadata["files"]) == 3
    assert "+third" in metadata["files"][2]["patch"]
    assert "+second" not in metadata["files"][2]["patch"]


def test_patch_update_then_delete_reports_final_deletion(tmp_path):
    backend = DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    (tmp_path / "x.txt").write_bytes(b"old\n")
    patch = "*** Begin Patch\n*** Update File: x.txt\n@@\n-old\n+new\n*** Delete File: x.txt\n*** End Patch"
    _, metadata = backend.apply_patch(patch, lambda _: None)
    assert [item["type"] for item in metadata["applied"]] == ["update", "delete"]
    assert len(metadata["files"]) == 1
    assert metadata["files"][0]["status"] == "deleted"
    assert "-old" in metadata["files"][0]["patch"]
    assert not (tmp_path / "x.txt").exists()


def test_patch_preflight_tracks_creates_moves_and_deletes_in_order(tmp_path):
    backend = DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    patch = """*** Begin Patch
*** Add File: first.txt
+first
*** Update File: first.txt
*** Move to: second.txt
@@
-first
+second
*** Update File: second.txt
@@
-second
+final
*** End Patch"""
    _, metadata = backend.apply_patch(patch, lambda _: None)
    assert not (tmp_path / "first.txt").exists()
    assert (tmp_path / "second.txt").read_text() == "final\n"
    assert len(metadata["applied"]) == 3
    assert len(metadata["files"]) == 1
    assert metadata["files"][0]["status"] == "added"
    assert "+final" in metadata["files"][0]["patch"]


def test_patch_cannot_update_a_previously_deleted_file(tmp_path):
    target = tmp_path / "x.txt"
    target.write_bytes(b"original\n")
    backend = DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    patch = "*** Begin Patch\n*** Delete File: x.txt\n*** Update File: x.txt\n@@\n-original\n+changed\n*** End Patch"
    with pytest.raises(FileNotFoundError):
        backend.apply_patch(patch, lambda _: None)
    assert target.read_bytes() == b"original\n"


def test_directory_offset_past_end_returns_empty_page(tmp_path, monkeypatch):
    agent = build_agent(
        tmp_path,
        monkeypatch,
        [
            ("read_file", {"file_path": "/", "offset": 20, "limit": 1}),
        ],
    )
    result = agent.invoke(
        {"messages": [HumanMessage(content="Read directory")]},
        {"configurable": {"thread_id": "directory-past-end"}},
    )
    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == "success"
    assert "0 entries" in message.content


def test_cr_only_edit_compatibility_and_explicit_newline_change(tmp_path):
    backend = DocumentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    target = tmp_path / "legacy.txt"
    target.write_bytes(b"first\rsecond\r")
    assert backend.edit("/legacy.txt", "first\nsecond", "first\nnew").error is None
    assert target.read_bytes() == b"first\rnew\r"
    assert backend.edit("/legacy.txt", "\r", "\n", replace_all=True).error is None
    assert target.read_bytes() == b"first\nnew\n"
