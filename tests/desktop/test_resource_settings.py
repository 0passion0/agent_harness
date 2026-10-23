import asyncio
from pathlib import Path
from uuid import uuid4

from vertical_harness.desktop import AgentLoop


def test_resource_settings_work_without_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    workspace = tmp_path / "project"
    workspace.mkdir()
    loop = AgentLoop()

    async def exercise():
        global_params = {"scope": "global", "workspace_root": None}
        project_params = {"scope": "project", "workspace_root": str(workspace)}
        result = await loop.dispatch(
            "harness.save",
            {**global_params, "kind": "agent", "name": "", "content": "global constraints"},
        )
        assert result["agent"]["content"] == "global constraints"
        assert (await loop.dispatch("harness.inspect", project_params))["agent"]["exists"] is False
        assert (
            await loop.dispatch("harness.remove", {**global_params, "kind": "agent", "name": ""})
        )["agent"]["exists"] is False

    asyncio.run(exercise())


def test_new_desktop_turn_refreshes_skill_metadata(tmp_path, monkeypatch):
    from test_bridge import configure

    from vertical_harness.runtime.run_manager import RunManager

    loop = AgentLoop()
    received = []

    async def capture(_self, thread_id, run_id, inputs):
        received.append(inputs)

    monkeypatch.setattr(RunManager, "_run", capture)

    async def exercise():
        await configure(monkeypatch, tmp_path, loop)
        try:
            await loop.dispatch(
                "run.start",
                {"thread_id": str(uuid4()), "run_id": str(uuid4()), "text": "use updated skills"},
            )
            await loop.runs.wait()
        finally:
            await loop.aclose()

    asyncio.run(exercise())
    assert received[0]["skills_metadata"] is None
    assert received[0]["messages"][0].content == "use updated skills"
