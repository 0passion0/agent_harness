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
from vertical_harness.config.settings import HarnessSettings
from vertical_harness.core.compose import AgentComposer
from vertical_harness.core.context import RunContext
from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.log.logging import HarnessLogging

logger = logging.getLogger(__name__)


class Cli:
    """命令行界面驱动类，提供本地终端多模态输入解析与交互会话执行。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    def image_content(value: str) -> dict[str, str]:
        """读取受支持的本地图片文件并返回标准化 base64 内容字典。

        :param value: 本地图片文件的路径字符串。
        :type value: str
        :return: 包含 type、base64 与 mime_type 的字典。
        :rtype: dict[str, str]
        :raises argparse.ArgumentTypeError: 当图片格式不受支持、文件无法读取或超过 20MB 时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：解析文件路径并检测 MIME 类型
        logger.debug("parsing_image_argument")
        path = Path(value).expanduser()
        mime_type = mimetypes.guess_type(path.name)[0]
        if mime_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
            raise argparse.ArgumentTypeError("Image must be PNG, JPEG, GIF or WebP")
        max_bytes = 20 * 1024 * 1024

        # 第二步：受控读取二进制数据并校验文件大小
        try:
            with path.open("rb") as source:
                data = source.read(max_bytes + 1)
        except OSError as exc:
            raise argparse.ArgumentTypeError("Cannot read image file") from exc
        if not data or len(data) > max_bytes:
            raise argparse.ArgumentTypeError("Image must be nonempty and at most 20 MiB")

        # 第三步：转为 base64 并返回结构化图像块
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

        :param argv: 可选的命令行参数列表，未提供时默认从 sys.argv 解析。
        :type argv: list[str] | None
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：构建参数解析器并解析输入
        parser = argparse.ArgumentParser(description="运行智能体对话命令")
        parser.add_argument("prompt", nargs="?", default="")
        parser.add_argument("--image", action="append", type=Cli.image_content, default=[])
        parser.add_argument("--thread-id", default=str(uuid4()))
        parser.add_argument("--user-id", default="default")
        parser.add_argument("--tenant-id", default="default")
        args = parser.parse_args(argv)
        if not args.prompt.strip() and not args.image:
            parser.error("Provide a prompt or --image")

        # 第二步：加载并校验宿主配置，初始化系统日志
        try:
            settings = HarnessSettings()
        except ValidationError:
            parser.error("Invalid harness configuration")
        HarnessLogging.configure_logging(settings)

        # 第三步：组装文本与多模态载荷并进入异步执行
        content = [{"type": "text", "text": args.prompt}, *args.image] if args.image else args.prompt
        asyncio.run(Cli.run(settings, args, content))

    @staticmethod
    async def run(settings: HarnessSettings, args: argparse.Namespace, content: str | list) -> None:
        """异步执行智能体调用并在标准输出打印最终回复文本。

        :param settings: 宿主运行配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :param args: 解析后的命令行命名空间对象。
        :type args: argparse.Namespace
        :param content: 文本提示词或包含多模态块的列表。
        :type content: str | list
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：以异步上下文方式打开智能体
        async with AgentComposer.aopen_agent(settings) as agent:
            # 第二步：调用 ainvoke_agent 执行状态图并打印最后一条助手回复
            result = await AgentStreaming.ainvoke_agent(
                agent,
                {"messages": [("user", content)]},
                config=CheckpointConfig.make_checkpoint_config(args.thread_id),
                context=RunContext(tenant_id=args.tenant_id, user_id=args.user_id),
            )
            print(result["messages"][-1].text)


_image_content = Cli.image_content
main = Cli.main

__all__ = ["Cli", "_image_content", "main"]
