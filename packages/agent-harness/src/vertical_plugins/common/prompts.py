"""领域插件系统指令的公共拼装规则模块。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def format_plugin_instruction(plugin_id: str, instructions: str) -> str | None:
    """生成带有插件命名空间标识的系统提示词段落。

    :param plugin_id: 插件唯一标识符。
    :type plugin_id: str
    :param instructions: 插件贡献的原始指令文本。
    :type instructions: str
    :return: 若指令去除首尾空白后非空则返回 ``[plugin_id] 指令`` 格式字符串，否则返回 None。
    :rtype: str | None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：去除首尾空白字符
    cleaned = instructions.strip()
    logger.debug(
        "plugin_system_instruction_formatted",
        extra={"event_fields": {"plugin_id": plugin_id, "has_instruction": bool(cleaned)}},
    )
    # 第二步：若包含有效指令，附加命名空间前缀输出
    return f"[{plugin_id}] {cleaned}" if cleaned else None


__all__ = ["format_plugin_instruction"]
