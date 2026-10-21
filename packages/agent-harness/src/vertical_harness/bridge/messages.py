"""桌面端多模态消息转换与消息序列化模块。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class DesktopMessages:
    """桌面端消息解析与格式化工具类。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    def attachment_message(
            workspace_root: Path, text: str, attachments: list[dict[str, str]]
    ) -> str | list[dict[str, Any]]:
        """校验附件安全性（必须在工作区物理边界内）并生成多模态消息内容块。

        :param workspace_root: 当前工作区的物理根目录 Path 对象。
        :type workspace_root: pathlib.Path
        :param text: 用户输入的原始提示词。
        :type text: str
        :param attachments: 附件字典列表，每项必须包含 name、path 与 media_type 字段。
        :type attachments: list[dict[str, str]]
        :return: 原样文本字符串或多模态消息块列表。
        :rtype: str | list[dict[str, typing.Any]]
        :raises ValueError: 当附件缺少必填字段、路径非绝对、文件不存在或超出工作区时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：解析工作区真实绝对路径
        root = workspace_root.expanduser().resolve()
        validated = []

        # 第二步：逐一校验附件元数据结构及文件存在性
        for attachment in attachments:
            if not isinstance(attachment, dict) or set(attachment) != {"name", "path", "media_type"}:
                raise ValueError("attachments must contain name, path, and media_type")
            if any(not isinstance(attachment[key], str) for key in attachment):
                raise ValueError("attachment fields must be strings")
            source = Path(attachment["path"])
            if not source.is_absolute():
                raise ValueError("attachment path must be absolute")
            source = source.resolve(strict=True)
            if not source.is_file():
                raise ValueError("attachment path must refer to a file")
            validated.append((attachment, source))

        # 第三步：校验所有附件必须严格位于工作区目录之下
        sources = []
        for attachment, source in validated:
            try:
                source.relative_to(root)
            except ValueError as exc:
                raise ValueError("attachment must be inside the workspace") from exc
            sources.append((attachment, source))

        # 第四步：调用 content_blocks 生成标准多模态内容块
        from vertical_harness.bridge.attachments import AttachmentContent

        return AttachmentContent.content_blocks(sources, text, root)

    @staticmethod
    def raw_message(message: Any) -> dict[str, Any]:
        """将 LangChain 消息对象导出为排除空字段的标准 JSON 字典。

        :param message: LangChain 消息对象（如 HumanMessage、AIMessage 等）。
        :type message: typing.Any
        :return: 序列化后的消息字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        return message.model_dump(mode="json", exclude_none=True)
