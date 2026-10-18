"""MCP 服务连接配置；只允许显式选择 stdio 或 Streamable HTTP。"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, StringConstraints

MCPServerName = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")]


class _MCPServer(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)
    # 服务端声明的 readOnlyHint 不作为授权依据，由宿主显式指定副作用级别。
    effect: Literal["read", "write", "external"] = "external"
    allowed_tools: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]] | None = None
    # Native skill disclosure applies only to tools the host declares read-only.
    defer_tools: bool = False
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)] | None = None


class MCPStdioServer(_MCPServer):
    """通过受信任的本地命令启动 MCP 子进程，不经过 shell。"""

    transport: Literal["stdio"]
    command: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    args: list[str] = Field(default_factory=list, repr=False)
    env: dict[str, SecretStr] = Field(default_factory=dict)
    cwd: Path | None = None


class MCPHttpServer(_MCPServer):
    """Streamable HTTP 端点及显式 HTTP 请求头。"""

    transport: Literal["streamable_http", "http"]
    url: HttpUrl = Field(repr=False)
    headers: dict[str, SecretStr] = Field(default_factory=dict)


MCPServerConfig = Annotated[MCPStdioServer | MCPHttpServer, Field(discriminator="transport")]

__all__ = ["MCPHttpServer", "MCPServerConfig", "MCPServerName", "MCPStdioServer"]
