"""MCP 服务连接配置；只允许显式选择 stdio 或 Streamable HTTP。"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, StringConstraints

MCPServerName = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")]


class _MCPServer(BaseModel):
    """MCP 服务配置基类，定义通用超时、副作用级别及工具控制选项。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    # 请求超时时间（秒），必须大于 0
    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)
    # 服务端声明的 readOnlyHint 不作为授权依据，由宿主显式指定副作用级别（read, write, external）
    effect: Literal["read", "write", "external"] = "external"
    # 允许暴露的工具名称白名单（为 None 时允许全部）
    allowed_tools: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]] | None = None
    # 是否推迟工具暴露（仅限宿主标记为只读的工具）
    defer_tools: bool = False
    # 服务描述文本
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)] | None = None


class MCPStdioServer(_MCPServer):
    """通过受信任的本地命令行启动 MCP 子进程的标准输入输出（stdio）配置模型。"""

    # 传输协议标识符
    transport: Literal["stdio"]
    # 启动可执行文件命令（不经过 shell）
    command: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    # 启动参数列表
    args: list[str] = Field(default_factory=list, repr=False)
    # 环境变量键值字典（敏感信息加密隐藏）
    env: dict[str, SecretStr] = Field(default_factory=dict)
    # 子进程工作目录绝对路径
    cwd: Path | None = None


class MCPHttpServer(_MCPServer):
    """通过 Streamable HTTP / Server-Sent Events 连接远端 MCP 服务的配置模型。"""

    # 传输协议标识符
    transport: Literal["streamable_http", "http"]
    # 远端端点 URL
    url: HttpUrl = Field(repr=False)
    # 请求头字典（包含鉴权令牌等敏感信息）
    headers: dict[str, SecretStr] = Field(default_factory=dict)


MCPServerConfig = Annotated[MCPStdioServer | MCPHttpServer, Field(discriminator="transport")]

__all__ = ["MCPHttpServer", "MCPServerConfig", "MCPServerName", "MCPStdioServer"]
