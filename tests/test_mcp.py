"""MCP 配置、传输、工具注册、审批与生命周期回归。"""

import asyncio
import json
import socket
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import ValidationError

from vertical_harness import (
    HarnessSettings,
    MCPHttpServer,
    MCPStdioServer,
    PluginContribution,
    RunContext,
    ToolContribution,
    aopen_agent,
    cli,
)
from vertical_harness.engine import compose
from vertical_harness.engine.mcp import aopen_mcp_tools


@pytest.mark.parametrize("effect,defer,gating,expected", [
    ("read", True, True, "skill"),
    ("read", False, True, "eager"),
    ("read", True, False, "eager"),
    ("external", True, True, "eager"),
    ("write", True, True, "eager"),
])
def test_mcp_disclosure_preserves_host_approval(tmp_path, fake_adapters, effect, defer, gating, expected):
    config = settings(tmp_path, enable_skill_gating=gating, mcp_servers={
        "local": {"transport": "stdio", "command": "python", "effect": effect, "defer_tools": defer},
    })

    async def run():
        async with aopen_mcp_tools(config) as tools:
            assert tools[0].exposure == expected
            assert tools[0].requires_approval == (effect != "read")

    asyncio.run(run())
    assert fake_adapters == [("enter", 0), ("exit", 0)]


@pytest.mark.parametrize("allowed", [[], ["missing"], ["echo"]])
def test_mcp_allowlist_and_cleanup(tmp_path, fake_adapters, allowed):
    config = settings(tmp_path, mcp_servers={
        "local": {"transport": "stdio", "command": "python", "allowed_tools": allowed},
    })

    async def run():
        async with aopen_mcp_tools(config) as tools:
            assert [item.name for item in tools] == (["local_echo"] if allowed else [])

    if allowed == ["missing"]:
        with pytest.raises(ValueError, match="Unknown allowed MCP tools"):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert fake_adapters == ([] if not allowed else [("enter", 0), ("exit", 0)])


def test_mcp_native_skill_disclosure_and_execution(tmp_path, fake_adapters, monkeypatch):
    from pydantic import Field

    from test_supervisor_workflows_and_time_travel import ToolCallingFakeModel
    from vertical_harness.engine.mcp import mcp_skill_assets

    class Model(ToolCallingFakeModel):
        bound_names: list = Field(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            self.bound_names.append([item["name"] if isinstance(item, dict) else item.name for item in tools])
            return self

    config = settings(tmp_path, enable_memory=False, mcp_servers={
        "local": {"transport": "stdio", "command": "python", "effect": "read", "defer_tools": True},
    })

    async def run():
        async with aopen_mcp_tools(config) as tools:
            skill = mcp_skill_assets(config, tools)[0]
            model = Model(responses=[
                AIMessage(content="", tool_calls=[{
                    "name": "read_file", "args": {"file_path": skill.to_virtual_path("harness")}, "id": "read-skill",
                }]),
                AIMessage(content="", tool_calls=[{
                    "name": "local_echo", "args": {"value": "hello"}, "id": "echo",
                }]),
                AIMessage(content="done"),
            ])
            monkeypatch.setattr(compose, "init_configured_model", lambda *_: model)
            agent = compose.build_agent(config, mcp_tools=tools)
            result = await agent.ainvoke(
                {"messages": [("user", "echo hello")]},
                config={"configurable": {"thread_id": "deferred"}},
            )
            assert not result.get("skills_load_errors")
            assert "local_echo" not in model.bound_names[0]
            assert "local_echo" in model.bound_names[1]
            output = next(message for message in result["messages"] if message.name == "local_echo")
            assert output.content == "hello"
            assert output.status == "success"
            assert result["messages"][-1].text == "done"
            # Rebuilding without the integration must not advertise stale skill files.
            fresh = compose.build_agent(config.model_copy(update={"mcp_servers": {}}))
            result = await fresh.ainvoke(
                {"messages": [("user", "new task")]},
                config={"configurable": {"thread_id": "new-catalog"}},
            )
            assert not result.get("skills_metadata")
            assert "local_echo" not in model.bound_names[-1]
            old_output = next(message for message in result["messages"] if message.name == "local_echo")
            assert old_output.status == "error"

    asyncio.run(run())


def settings(tmp_path, **kwargs):
    kwargs.setdefault("store_backend", "memory")
    return HarnessSettings(
        _env_file=None,
        model="test:mcp",
        workspace_root=tmp_path,
        enabled_plugins=[],
        **kwargs,
    )


def test_mcp_env_configuration_and_secret_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VA_MCP_SERVERS",
        json.dumps(
            {
                "local": {
                    "transport": "stdio",
                    "command": "python",
                    "args": ["server.py"],
                    "env": {"KEY": "secret-env"},
                },
                "remote": {
                    "transport": "streamable_http",
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "secret-header"},
                },
            }
        ),
    )
    config = settings(tmp_path)
    assert isinstance(config.mcp_servers["local"], MCPStdioServer)
    assert isinstance(config.mcp_servers["remote"], MCPHttpServer)
    assert config.mcp_servers["local"].env["KEY"].get_secret_value() == "secret-env"
    assert "secret-env" not in repr(config)
    assert "secret-header" not in config.model_dump_json()
    assert config.mcp_servers["remote"].effect == "external"


@pytest.mark.parametrize(
    "server",
    [
        {"transport": "stdio"},
        {"transport": "stdio", "command": "  "},
        {"transport": "stdio", "command": "python", "url": "http://localhost/mcp"},
        {"transport": "streamable_http", "url": "file:///tmp/server.py"},
        {"transport": "streamable_http", "url": "http://localhost/mcp", "command": "python"},
        {"transport": "sse", "url": "http://localhost/sse"},
        {"transport": "http", "url": "http://localhost/mcp", "timeout": 0},
        {"transport": "http", "url": "http://localhost/mcp", "timeout": float("inf")},
        {"transport": "http", "url": "http://localhost/mcp", "effect": "unknown"},
    ],
)
def test_invalid_mcp_configuration(tmp_path, server):
    with pytest.raises(ValidationError):
        settings(tmp_path, mcp_servers={"server": server})


def test_mcp_sync_build_cannot_silently_ignore_servers(tmp_path):
    config = settings(tmp_path, mcp_servers={"local": {"transport": "stdio", "command": "python"}})
    with pytest.raises(ValueError, match="aopen_agent"):
        compose.build_agent(config)


@tool
async def echo(value: str) -> str:
    """Echo a test value."""
    return value


def test_mcp_annotations_are_advisory_metadata():
    annotations = {
        "title": "Lookup records",
        "read_only_hint": True,
        "destructive_hint": False,
        "idempotent_hint": True,
        "open_world_hint": False,
    }
    item = ToolContribution(
        tool=echo.model_copy(
            update={
                "metadata": {
                    "mcp": {"tool": {"annotations": annotations, "_meta": {"custom": "kept"}}},
                }
            }
        ),
        effect="external",
    )
    assert item.mcp_annotations == annotations
    assert item.requires_approval
    config = item.to_interrupt_config()
    assert config["allowed_decisions"] == ["approve", "reject"]
    assert "untrusted hints" in config["description"]
    assert json.dumps(annotations, ensure_ascii=False) in config["description"]
    assert item.tool.metadata["mcp"]["tool"]["_meta"] == {"custom": "kept"}
    assert not ToolContribution(tool=item.tool, effect="read").requires_approval


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"mcp": {}},
        {"mcp": None},
        {"mcp": {"tool": {}}},
        {"mcp": {"tool": None}},
        {"mcp": {"tool": {"annotations": "invalid"}}},
    ],
)
def test_missing_mcp_annotations_preserve_approval_config(metadata):
    item = ToolContribution(tool=echo.model_copy(update={"metadata": metadata}), effect="external")
    assert item.mcp_annotations is None
    assert item.to_interrupt_config() == {"allowed_decisions": ["approve", "reject"]}


@pytest.fixture
def fake_adapters(monkeypatch):
    import langchain.mcp

    events = []

    @asynccontextmanager
    async def adapter(client):
        index = sum(event[0] == "enter" for event in events)
        events.append(("enter", index))
        try:
            yield SimpleNamespace(list_tools=lambda: list_tools(index))
        finally:
            events.append(("exit", index))

    async def list_tools(index):
        if index == 1:
            raise RuntimeError("discovery failed")
        return [echo]

    monkeypatch.setattr(langchain.mcp, "MCPAdapter", adapter)
    return events


def test_partial_discovery_failure_closes_all_connections(tmp_path, fake_adapters):
    config = settings(
        tmp_path,
        mcp_servers={
            name: {"transport": "stdio", "command": "python"} for name in ["first", "second"]
        },
    )

    async def run():
        async with aopen_mcp_tools(config):
            pytest.fail("Discovery failure must propagate")

    with pytest.raises(RuntimeError, match="discovery failed"):
        asyncio.run(run())
    assert fake_adapters == [("enter", 0), ("enter", 1), ("exit", 1), ("exit", 0)]


@pytest.mark.parametrize("failure", ["build", "run", "cancel"])
def test_agent_failure_releases_mcp_connections(tmp_path, fake_adapters, monkeypatch, failure):
    config = settings(tmp_path, mcp_servers={"local": {"transport": "stdio", "command": "python"}})

    def build(*args, **kwargs):
        assert kwargs["mcp_tools"][0].name == "local_echo"
        assert kwargs["mcp_tools"][0].requires_approval
        if failure == "build":
            raise RuntimeError("build failed")
        return object()

    monkeypatch.setattr(compose, "build_agent", build)

    async def run():
        async with aopen_agent(config):
            if failure == "cancel":
                raise asyncio.CancelledError
            raise RuntimeError("run failed")

    expected = asyncio.CancelledError if failure == "cancel" else RuntimeError
    with pytest.raises(expected):
        asyncio.run(run())
    assert fake_adapters == [("enter", 0), ("exit", 0)]


def test_supervisor_does_not_connect_mcp(tmp_path, fake_adapters):
    config = settings(
        tmp_path,
        supervisor_only=True,
        mcp_servers={
            "local": {"transport": "stdio", "command": "python"},
        },
    )

    async def run():
        async with aopen_mcp_tools(config) as tools:
            assert tools == []

    asyncio.run(run())
    assert fake_adapters == []


def test_mcp_plugin_name_conflict_closes_connection(tmp_path, fake_adapters, monkeypatch):
    plugin = SimpleNamespace(
        manifest=SimpleNamespace(plugin_id="test.conflict"),
        build=lambda services: PluginContribution(
            tools=[
                ToolContribution(tool=echo.model_copy(update={"name": "local_echo"})),
            ]
        ),
    )
    monkeypatch.setattr(compose, "discover_plugins", lambda enabled: [plugin])
    config = settings(tmp_path, mcp_servers={"local": {"transport": "stdio", "command": "python"}})

    async def run():
        async with aopen_agent(config):
            pytest.fail("Conflicting tools must not build")

    with pytest.raises(ValueError, match="Duplicate tool name: local_echo"):
        asyncio.run(run())
    assert fake_adapters == [("enter", 0), ("exit", 0)]


def test_mcp_cannot_shadow_builtin_tools(tmp_path):
    config = settings(tmp_path)
    with pytest.raises(ValueError, match="Reserved tool name: read_file"):
        compose.build_agent(
            config,
            mcp_tools=[
                ToolContribution(tool=echo.model_copy(update={"name": "read_file"})),
            ],
        )


@pytest.mark.parametrize("travel", [False, True])
def test_cli_uses_async_mcp_path(tmp_path, monkeypatch, capsys, travel):
    config = settings(tmp_path, mcp_servers={"local": {"transport": "stdio", "command": "python"}})
    events = []

    async def invoke(*args, **kwargs):
        events.append("travel" if travel else "invoke")
        return {"messages": [AIMessage(content="MCP answer")]}

    @asynccontextmanager
    async def open_agent(*args, **kwargs):
        events.append("open")
        try:
            yield SimpleNamespace(ainvoke=invoke)
        finally:
            events.append("close")

    monkeypatch.setattr(cli, "HarnessSettings", lambda: config)
    monkeypatch.setattr(cli, "aopen_agent", open_agent)
    monkeypatch.setattr(cli, "atime_travel_invoke", invoke)
    argv = ["query"] + (
        ["--thread-id", "thread", "--checkpoint-id", "checkpoint"] if travel else []
    )
    cli.main(argv)
    assert capsys.readouterr().out == "MCP answer\n"
    assert events == ["open", "travel" if travel else "invoke", "close"]


@pytest.fixture
def stdio_server(tmp_path):
    script = tmp_path / "mcp_server.py"
    script.write_text(
        '''
import os
from fastmcp import FastMCP

server = FastMCP("Harness test")
calls = 0

@server.tool(annotations={
    "title": "Local echo", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False,
})
def echo(value: str) -> str:
    """Echo a value with process-local state and configured environment."""
    global calls
    calls += 1
    return f"{os.environ.get('MCP_TEST_VALUE')}:{os.getcwd()}:{value}:{calls}"

if __name__ == "__main__":
    server.run(show_banner=False)
''',
        encoding="utf-8",
    )
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(script)],
        "cwd": str(tmp_path),
        "env": {"MCP_TEST_VALUE": "configured"},
    }


def test_real_stdio_and_streamable_http(tmp_path, stdio_server, monkeypatch):
    import uvicorn
    from fastmcp import FastMCP
    from fastmcp.client import transports
    from fastmcp.server.dependencies import get_http_headers

    transports_opened = []
    stdio_transport = transports.StdioTransport

    def track_stdio(**kwargs):
        transport = stdio_transport(**kwargs)
        transports_opened.append(transport)
        return transport

    monkeypatch.setattr(transports, "StdioTransport", track_stdio)
    server = FastMCP("HTTP test")

    @server.tool(
        annotations={
            "title": "HTTP echo",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    def echo(value: str) -> str:
        """Return a value and a configured HTTP header."""
        return f"{get_http_headers().get('x-test-token')}:{value}"

    async def run():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            http_server = uvicorn.Server(uvicorn.Config(server.http_app(), log_level="error"))
            task = asyncio.create_task(http_server.serve(sockets=[sock]))
            try:
                async with asyncio.timeout(10):
                    while not http_server.started:
                        if task.done():
                            await task
                            raise RuntimeError("HTTP server exited before startup")
                        await asyncio.sleep(0.01)
                config = settings(
                    tmp_path,
                    mcp_servers={
                        "local": stdio_server,
                        "remote": {
                            "transport": "http",
                            "url": f"http://127.0.0.1:{port}/mcp",
                            "headers": {"X-Test-Token": "configured-http"},
                            "effect": "read",
                        },
                    },
                )
                async with aopen_mcp_tools(config) as contributions:
                    tools = {item.name: item for item in contributions}
                    assert set(tools) == {"local_echo", "remote_echo"}
                    assert tools["local_echo"].requires_approval
                    assert not tools["remote_echo"].requires_approval
                    assert tools["local_echo"].mcp_annotations == {
                        "title": "Local echo",
                        "read_only_hint": True,
                        "destructive_hint": False,
                        "idempotent_hint": False,
                        "open_world_hint": False,
                    }
                    assert tools["remote_echo"].mcp_annotations == {
                        "title": "HTTP echo",
                        "read_only_hint": True,
                        "destructive_hint": False,
                        "idempotent_hint": True,
                        "open_world_hint": False,
                    }
                    for index in [1, 2]:
                        local = await tools["local_echo"].tool.ainvoke({"value": "hello"})
                        assert f":hello:{index}" in str(local)
                    remote = await tools["remote_echo"].tool.ainvoke({"value": "hello"})
                    assert "configured-http:hello" in str(remote)
                assert transports_opened[0]._connect_task is None
                assert transports_opened[0]._session is None
            finally:
                http_server.should_exit = True
                await asyncio.wait_for(task, timeout=10)

    asyncio.run(run())


def test_real_stdio_tool_and_approval_resume(tmp_path, stdio_server, monkeypatch):
    from test_supervisor_workflows_and_time_travel import ToolCallingFakeModel

    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "local_echo",
                        "args": {"value": "hello"},
                        "id": "echo-call",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda settings: model)
    config = settings(tmp_path, store_backend="sqlite", mcp_servers={"local": stdio_server})

    async def run():
        run_config = {"configurable": {"thread_id": "mcp-approval"}}
        context = RunContext(tenant_id="tenant", user_id="user")
        async with aopen_agent(config) as agent:
            interrupted = await agent.ainvoke(
                {"messages": [HumanMessage(content="echo hello")]},
                config=run_config,
                context=context,
            )
            assert "__interrupt__" in interrupted
            request = interrupted["__interrupt__"][0].value["action_requests"][0]
            assert request["name"] == "local_echo"
            assert request["args"] == {"value": "hello"}
            assert '"title": "Local echo"' in request["description"]
            assert '"read_only_hint": true' in request["description"]
            assert "untrusted hints" in request["description"]
        async with aopen_agent(config) as agent:
            resumed = await agent.ainvoke(
                Command(resume={"decisions": [{"type": "approve"}]}),
                config=run_config,
                context=context,
            )
            message = next(msg for msg in resumed["messages"] if msg.type == "tool")
            assert message.content[0]["text"] == f"configured:{tmp_path}:hello:1"
            assert resumed["messages"][-1].content == "done"

    asyncio.run(run())
