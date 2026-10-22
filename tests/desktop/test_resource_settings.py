import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from vertical_harness.core.context import RunContext
from vertical_harness.desktop import AgentLoop


def test_resource_settings_work_without_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    workspace = tmp_path / "project"
    workspace.mkdir()
    loop = AgentLoop()

    async def exercise():
        global_params = {"scope": "global", "workspace_root": None}
        project_params = {"scope": "project", "workspace_root": str(workspace)}
        result = await loop.dispatch("harness.save", {
            **global_params, "kind": "agent", "name": "", "content": "global constraints"
        })
        assert result["agent"]["content"] == "global constraints"
        assert (await loop.dispatch("harness.inspect", project_params))["agent"]["exists"] is False
        loop.active["running"] = ("thread", None)
        with pytest.raises(RuntimeError, match="active"):
            await loop.dispatch("harness.remove", {**global_params, "kind": "agent", "name": ""})
        loop.active.clear()
        assert (await loop.dispatch("harness.remove", {
            **global_params, "kind": "agent", "name": ""
        }))["agent"]["exists"] is False

    asyncio.run(exercise())


def test_new_desktop_turn_refreshes_skill_metadata(tmp_path, monkeypatch):
    from vertical_harness.config.settings import HarnessSettings

    loop = AgentLoop()
    loop.settings = HarnessSettings(workspace_root=tmp_path)
    loop.identity = RunContext(tenant_id="test", user_id="test")
    received = []

    async def capture(thread_id, run_id, inputs):
        received.append(inputs)

    monkeypatch.setattr(loop, "_run", capture)

    async def exercise():
        await loop.dispatch("run.start", {
            "thread_id": str(uuid4()), "run_id": str(uuid4()), "text": "use updated skills"
        })
        await asyncio.gather(*(task for _, task in loop.active.values()))

    asyncio.run(exercise())
    assert received[0]["skills_metadata"] is None
    assert received[0]["messages"][0].content == "use updated skills"
