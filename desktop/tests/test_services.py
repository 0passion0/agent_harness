from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from vertical_harness.engine.mcp import aopen_mcp_tools


def options(mcp_servers):
    return {
        "model_code": "", "exa_api_key": "",
        "runtimes": {"python": "", "node": ""}, "permission_mode": "full",
        "max_context_tokens": 128000, "agent_content": "", "skills": [],
        "mcp_servers": mcp_servers, "analysis_enabled": False,
        "exa_enabled": False, "analysis_plugin_dir": "",
    }


def test_bridge_discovers_and_calls_local_mcp_tool(tmp_path, monkeypatch):
    import bridge

    monkeypatch.setenv("VA_MODEL", "test:fake")
    fixture = Path(__file__).parent / "fixtures" / "mcp_server.py"
    python = sys.executable
    server = {
        "transport": "stdio", "command": str(python), "args": [str(fixture)],
        "cwd": str(fixture.parents[3]), "effect": "read",
    }

    async def scenario():
        await bridge.dispatch("configure", {
            "tenant_id": "tenant", "user_id": "user",
            "workspace_root": str(tmp_path / "workspace"),
            "runtime_data_dir": str(tmp_path / "runtime"),
            "options": options({"local": server}),
        })
        assert await bridge.dispatch("mcp.test", {"server_id": "local"}) == {
            "servers": [{"name": "local", "tools": ["local_echo"]}],
        }
        async with aopen_mcp_tools(bridge.settings) as tools:
            echo = next(item.tool for item in tools if item.name == "local_echo")
            result = await echo.ainvoke({"value": "desktop MCP works"})
            assert result[0]["text"] == "desktop MCP works"

    asyncio.run(scenario())
