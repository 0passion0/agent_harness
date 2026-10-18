"""使用 LangChain 原生 MCPAdapter 管理工具发现和连接生命周期。"""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from vertical_harness.config import HarnessSettings, MCPStdioServer
from vertical_harness.plugins.contracts import ToolContribution

logger = logging.getLogger(__name__)


@asynccontextmanager
async def aopen_mcp_tools(settings: HarnessSettings) -> AsyncIterator[list[ToolContribution]]:
    """在同一异步任务内持有会话，退出或构建失败时关闭所有已打开的连接。"""
    contributions: list[ToolContribution] = []
    if not settings.mcp_servers or settings.supervisor_only:
        yield contributions
        return

    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
    from langchain.mcp import MCPAdapter

    async with AsyncExitStack() as stack:
        for name, server in settings.mcp_servers.items():
            fields = {"server_name": name, "transport": server.transport}
            logger.debug("mcp_server_connecting", extra={"event_fields": fields})
            if isinstance(server, MCPStdioServer):
                transport = StdioTransport(
                    command=server.command,
                    args=server.args,
                    env={key: value.get_secret_value() for key, value in server.env.items()},
                    cwd=str(server.cwd) if server.cwd is not None else None,
                    # 默认 keep_alive 会跨上下文保留子进程；宿主退出必须回收它。
                    keep_alive=False,
                )
            else:
                transport = StreamableHttpTransport(
                    url=str(server.url),
                    headers={
                        key: value.get_secret_value() for key, value in server.headers.items()
                    },
                )
            adapter = await stack.enter_async_context(
                MCPAdapter(Client(transport, timeout=server.timeout, init_timeout=server.timeout))
            )
            tools = await adapter.list_tools()
            for tool in tools:
                contributions.append(
                    ToolContribution(
                        tool=tool.model_copy(update={"name": f"{name}_{tool.name}"}),
                        effect=server.effect,
                    )
                )
            logger.info(
                "mcp_tools_loaded", extra={"event_fields": {**fields, "tool_count": len(tools)}}
            )
        try:
            yield contributions
        finally:
            logger.debug("mcp_connections_closing")


__all__ = ["aopen_mcp_tools"]
