"""Run one search/file/script agent from the command line."""

import argparse
import asyncio
import base64
import logging
import mimetypes
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from vertical_harness import (
    HarnessSettings,
    RunContext,
    ainvoke_agent,
    aopen_agent,
    configure_logging,
    make_checkpoint_config,
)

logger = logging.getLogger(__name__)


def _image_content(value: str) -> dict[str, str]:
    """读取本地图片为标准内容块，限制读取量以免附件耗尽内存。

    校验图片文件格式（支持 PNG、JPEG、GIF、WebP）及字节大小（<= 20 MiB），编码为 Base64 字典。

    :param value: 本地图片的文件路径字符串。
    :type value: str
    :return: 包含 Base64 编码数据与 MIME 类型的标准多模态内容块字典。
    :rtype: dict[str, str]
    :raises argparse.ArgumentTypeError: 当文件不存在、格式不受支持或大小超出 20 MiB 时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("parsing_image_argument")
    # 第一步：解析文件路径并检测 MIME 类型
    path = Path(value).expanduser()
    mime_type = mimetypes.guess_type(path.name)[0]
    if mime_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        raise argparse.ArgumentTypeError("Image must be PNG, JPEG, GIF or WebP")
    max_bytes = 20 * 1024 * 1024

    # 第二步：安全读取图片字节流并校验文件体积上限
    try:
        with path.open("rb") as source:
            data = source.read(max_bytes + 1)
    except OSError as exc:
        raise argparse.ArgumentTypeError("Cannot read image file") from exc
    if not data or len(data) > max_bytes:
        raise argparse.ArgumentTypeError("Image must be nonempty and at most 20 MiB")

    # 第三步：构建并返回 Base64 编码的多模态图片字典
    logger.debug(
        "image_content_parsed",
        extra={"event_fields": {"mime_type": mime_type, "byte_size": len(data)}},
    )
    return {
        "type": "image",
        "base64": base64.b64encode(data).decode("ascii"),
        "mime_type": mime_type,
    }


def main(argv: list[str] | None = None) -> None:
    """Run or continue a conversation using native checkpoints."""
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default="")
    parser.add_argument("--image", action="append", type=_image_content, default=[])
    parser.add_argument("--thread-id", default=str(uuid4()))
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--tenant-id", default="default")
    args = parser.parse_args(argv)
    if not args.prompt.strip() and not args.image:
        parser.error("Provide a prompt or --image")
    try:
        settings = HarnessSettings()
    except ValidationError:
        parser.error("Invalid harness configuration")
    configure_logging(settings)
    content = [{"type": "text", "text": args.prompt}, *args.image] if args.image else args.prompt

    async def run():
        async with aopen_agent(settings) as agent:
            result = await ainvoke_agent(
                agent,
                {"messages": [("user", content)]},
                config=make_checkpoint_config(args.thread_id),
                context=RunContext(tenant_id=args.tenant_id, user_id=args.user_id),
            )
            print(result["messages"][-1].text)

    asyncio.run(run())
