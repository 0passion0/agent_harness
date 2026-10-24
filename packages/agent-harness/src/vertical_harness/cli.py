"""通过命令行终端启动搜索、工作区文件与受控脚本智能体的 CLI 入口模块。"""

import argparse
import asyncio
import base64
import logging
import mimetypes
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from vertical_harness.config.checkpoint_config import CheckpointConfig
from vertical_harness.core.compose import AgentComposer
from vertical_harness.core.context import RunContext
from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.log.logging import HarnessLogging
from vertical_harness.models import HarnessSettings

logger = logging.getLogger(__name__)


class Cli:
    """命令行界面驱动类，提供本地终端多模态输入解析与交互会话执行。"""

    @staticmethod
    def image_content(value: str) -> dict[str, str]:
        """读取受支持的本地图片文件并返回标准化 base64 内容字典。

        Args:
            value: 本地图片文件的路径字符串，支持 '~' 展开。

        Returns:
            标准化多模态图片字典，包含以下字段：
                - 'type': 固定为 'image'。
                - 'base64': 图片原始二进制数据的 Base64 编码 ASCII 字符串。
                - 'mime_type': 检测到的 MIME 类型字符串（如 'image/png'）。

        Raises:
            argparse.ArgumentTypeError: 文件格式不受支持、文件无法读取、文件为空或超过 20 MiB 时抛出。
        """
        logger.debug("parsing_image_argument")
        path = Path(value).expanduser()
        mime_type = mimetypes.guess_type(path.name)[0]
        # 严格校验媒体格式白名单
        if mime_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
            raise argparse.ArgumentTypeError("Image must be PNG, JPEG, GIF or WebP")
        # 单张图片输入字节上限（20 MiB），超限拒绝以保护模型上下文预算
        max_bytes = 20 * 1024 * 1024
        try:
            with path.open("rb") as source:
                data = source.read(max_bytes + 1)
        except OSError as exc:
            raise argparse.ArgumentTypeError("Cannot read image file") from exc
        # 校验图片大小在有效区间 (0, 20 MiB]
        if not data or len(data) > max_bytes:
            raise argparse.ArgumentTypeError("Image must be nonempty and at most 20 MiB")
        logger.debug(
            "image_content_parsed",
            extra={"event_fields": {"mime_type": mime_type, "byte_size": len(data)}},
        )
        return {
            "type": "image",
            "base64": base64.b64encode(data).decode("ascii"),
            "mime_type": mime_type,
        }

    @staticmethod
    def main(argv: list[str] | None = None) -> None:
        """解析命令行参数、加载配置并启动异步主运行循环。

        Args:
            argv: 命令行参数字符串列表；为 None 时默认读取 sys.argv[1:]。

        Raises:
            SystemExit: 参数校验失败或配置加载错误时由 argparse 触发退出。
        """
        # 初始化命令行参数解析器
        parser = argparse.ArgumentParser(description="运行智能体对话命令")
        parser.add_argument("prompt", nargs="?", default="")
        parser.add_argument("--image", action="append", type=Cli.image_content, default=[])
        parser.add_argument("--thread-id", default=str(uuid4()))
        parser.add_argument("--user-id", default="default")
        parser.add_argument("--tenant-id", default="default")
        args = parser.parse_args(argv)

        # 校验输入有效性：prompt 与 image 不可同时为空
        if not args.prompt.strip() and not args.image:
            parser.error("Provide a prompt or --image")

        # 加载并校验环境变量及应用配置
        try:
            settings = HarnessSettings.model_validate({})
        except ValidationError:
            parser.error("Invalid harness configuration")

        # 配置全局日志系统
        HarnessLogging.configure_logging(settings)

        # 构造用户输入载荷：若存在图片则组装为多模态内容块列表，否则使用纯文本
        content = (
            [{"type": "text", "text": args.prompt}, *args.image] if args.image else args.prompt
        )
        asyncio.run(Cli.run(settings, args, content))

    @staticmethod
    async def run(settings: HarnessSettings, args: argparse.Namespace, content: str | list) -> None:
        """异步执行智能体调用并在标准输出打印最终回复文本。

        Args:
            settings: 经验证的应用宿主全局配置实体。
            args: 解析后的命令行参数命名空间，包含 thread_id, tenant_id, user_id。
            content: 用户输入内容，为纯文本字符串或多模态消息块列表。

        Returns:
            None
        """
        # 通过上下文管理器管理智能体状态图生命周期
        async with AgentComposer.aopen_agent(settings) as agent:
            result = await AgentStreaming.ainvoke_agent(
                agent,
                {"messages": [("user", content)]},
                config=CheckpointConfig.make_checkpoint_config(args.thread_id),
                context=RunContext(tenant_id=args.tenant_id, user_id=args.user_id),
            )
            # 提取最后一条消息的正文输出至终端标准输出
            print(result["messages"][-1].text)
