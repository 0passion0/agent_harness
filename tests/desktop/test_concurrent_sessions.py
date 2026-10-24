"""真实图验证并行会话、配置快照及暂停恢复的隔离。"""

import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from test_background_lifecycle import request

from test_deepagents_architecture import Model
from vertical_harness.core import compose
from vertical_harness.desktop import AgentLoop


def test_parallel_sessions_cancel_only_target_and_keep_project_resources(tmp_path, monkeypatch):
    entered = set()
    both = asyncio.Event()
    releases = {name: asyncio.Event() for name in ("first", "second")}

    class BlockingModel(Model):
        async def _agenerate(self, messages, *args, **kwargs):
            prompt = next(m.content for m in messages if isinstance(m, HumanMessage))
            if prompt in releases:
                entered.add(prompt)
                if len(entered) == 2:
                    both.set()
                await releases[prompt].wait()
            return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])

    monkeypatch.setenv("VA_MODEL", "test:fake")
    monkeypatch.setattr(
        compose.ModelRouting, "init_configured_model", lambda _: BlockingModel(responses=[])
    )
    loop, events = AgentLoop(), []

    async def notify(method, data):
        events.append((method, data))

    loop.server.notify = notify

    async def run():
        try:
            config = request(tmp_path)
            await loop.dispatch("configure", config)
            manager, runtime = loop.runs, loop.runtime
            threads, ids = [str(uuid4()) for _ in range(2)], [str(uuid4()) for _ in range(2)]
            for thread, identity, prompt in zip(threads, ids, releases, strict=True):
                await loop.dispatch(
                    "run.start", {"thread_id": thread, "run_id": identity, "text": prompt}
                )
            tasks = [manager.active(thread).task for thread in threads]
            await asyncio.wait_for(both.wait(), 5)
            with pytest.raises(RuntimeError, match="thread"):
                manager.start(threads[0], str(uuid4()), HumanMessage("duplicate"))
            assert (await loop.dispatch("model.test", {}))["ok"]
            await loop.dispatch("configure", config)
            assert loop.runtime is runtime
            other = request(tmp_path / "other")
            other["tenant_id"] = "other-project"
            await loop.dispatch("configure", other)
            assert loop.runtime is not runtime
            assert manager.active(threads[1]).task is tasks[1]
            other_thread, other_run = str(uuid4()), str(uuid4())
            await loop.dispatch(
                "run.start",
                {"thread_id": other_thread, "run_id": other_run, "text": "other project"},
            )
            await loop.runs.wait()
            assert all(not task.done() for task in tasks)
            assert any(
                data.get("thread_id") == other_thread and data.get("type") == "run.completed"
                for _method, data in events
            )
            await loop.dispatch("configure", config)
            await loop.dispatch("run.stop", {"thread_id": threads[0], "run_id": ids[0]})
            assert tasks[0].cancelled()
            assert not tasks[1].done()
            releases["second"].set()
            await asyncio.wait_for(tasks[1], 5)
            assert not manager.is_running
            terminal = [
                (method, data.get("thread_id"), data.get("type")) for method, data in events
            ]
            assert ("run.cancelled", threads[0], None) in terminal
            assert ("agent.event", threads[1], "run.completed") in terminal
        finally:
            await loop.aclose()

    asyncio.run(run())


def test_paused_run_restores_frozen_policy_after_configuration_change_and_restart(
    tmp_path, monkeypatch
):
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "write",
                        "name": "write_file",
                        "args": {"file_path": "/result.txt", "content": "private"},
                    }
                ],
            ),
            AIMessage("rejected"),
        ]
    )
    policies = []

    def configured(settings):
        policies.append((settings.permission_mode, settings.max_context_tokens))
        return model

    monkeypatch.setenv("VA_MODEL", "test:fake")
    monkeypatch.setenv("VA_API_KEY", "secret-do-not-persist")
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", configured)

    async def run():
        loop = AgentLoop()
        config = request(tmp_path)
        config["options"]["permission_mode"] = "ask"
        thread, identity = str(uuid4()), str(uuid4())
        try:
            await loop.dispatch("configure", config)
            await loop.dispatch(
                "run.start", {"thread_id": thread, "run_id": identity, "text": "write"}
            )
            await loop.runs.wait()
            snapshot = await loop.dispatch("snapshot", {"thread_id": thread})
            pending = snapshot["interrupts"][0]["id"]
            checkpoint = await loop.runtime.checkpointer.aget_tuple(
                {"configurable": {"thread_id": thread}}
            )
            frozen = json.loads(checkpoint.metadata["desktop_run_settings"])
            assert "api_key" not in frozen
            assert "secret-do-not-persist" not in str(checkpoint.metadata)
        finally:
            await loop.aclose()
        restored = AgentLoop()
        config["options"]["permission_mode"] = "full"
        config["options"]["max_context_tokens"] = 64000
        try:
            await restored.dispatch("configure", config)
            await restored.dispatch(
                "run.resume",
                {
                    "thread_id": thread,
                    "run_id": identity,
                    "decisions": {pending: {"type": "reject"}},
                },
            )
            await restored.runs.wait()
            assert policies[-1] == ("ask", 128000)
            assert restored.runtime.settings.permission_mode == "full"
            assert not (tmp_path / "workspace/result.txt").exists()
        finally:
            await restored.aclose()

    asyncio.run(run())
