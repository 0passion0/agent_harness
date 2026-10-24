"""模型上下文 Token 估算、按区块拆分统计与用量报告生成模块。

该模块提供基于消息体、工具定义以及模型上报元数据的上下文 Token 消耗估算能力，
能够将 Token 消耗细分拆解为系统提示词（`system_prompt`）、工具定义（`tools`）和历史上下文（`history`）
三大独立区块，并计算用量占比与剩余预算，为上下文截断、修剪和前端可视化仪表盘提供数据支撑。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages.utils import count_tokens_approximately


class UsageEstimator:
    """Token 上下文用量估算器。

    负责结合近似 Token 计算法与模型实际返回的 API 用量元数据，生成结构化的
    Token 消耗全景分析报告，支持多区块（系统提示词、工具列表、历史消息）的用量拆分与预算评估。
    """

    @staticmethod
    def usage_estimate(
        messages: list[Any],
        model: str,
        maximum: int = 475_000,
        options: dict[str, Any] | None = None,
        *,
        request_messages: list[Any] | None = None,
        tools: list[Any] | None = None,
        reported_tokens: int | None = None,
    ) -> dict[str, Any]:
        """估算或按模型真实上报用量拆分各区块（系统提示词、工具定义、历史消息）的 Token 构成与占比。

        该方法优先根据 `request_messages`（真实发给大模型的消息列表）提取系统提示词和历史消息；
        若未采集到真实请求消息，则退化为使用 `messages` 估算历史消息。若提供了模型服务商
        实际返回的 `reported_tokens`，则系统会以内容占比为权重，精确拆分并校准各个区块的 Token 份额。

        Args:
            messages: 会话历史消息列表，元素通常为 `BaseMessage` 及其子类（如 `HumanMessage`,
                `AIMessage`, `SystemMessage`, `ToolMessage`）。当 `request_messages` 为 `None`
                时，以此作为历史消息用量的输入数据源。
            model: 目标大语言模型标识字符串（如 `"gpt-4o"`、`"claude-3-5-sonnet"` 等），供调用方标识
                或未来针对特定模型的分词器微调。
            maximum: 模型允许的最大上下文窗口 Token 预算上限，默认为 475,000。
            options: 扩展配置字典（可选），包含用户自定义的统计或过滤选项，预留给未来特性扩展。
            request_messages: 最近一次实际向底层模型发送的完整请求消息列表（包含注入的系统提示词等）。
                若提供此参数，将标记为已采集（`captured=True`），能够精确剥离出系统提示词区块。
            tools: 绑定至模型的工具定义列表（如 LangChain `BaseTool` 实例或符合 OpenAI 规范的 JSON
                Schema 字典）。若提供，将通过近似估算法计算工具定义所占用的 Token 空间。
            reported_tokens: 模型服务商在 API 响应元数据中实际返回的精确 Token 消耗总量。若提供，
                系统将以此总量为基准，按各区块的内容估算比例将真实消耗分配至各区块。

        Returns:
            结构化的 Token 用量报告字典，包含以下字段与嵌套数据结构：
            - `max_tokens` (int): 模型最大 Token 容量上限。
            - `used_tokens` (int): 已消耗的 Token 总量（若提供 `reported_tokens` 则为其值，否则为估算值）。
            - `remaining_tokens` (int): 剩余可用的 Token 预算数（`max(0, maximum - used)`）。
            - `used_ratio` (float): 已消耗 Token 占最大容量的比例（0.0 ~ 1.0+）。
            - `remaining_ratio` (float): 剩余可用 Token 占最大容量的比例。
            - `sections` (dict[str, dict[str, Any]]): 按区块拆分的用量详情字典，包含以下键：
                - `"system_prompt"`: 系统提示词区块统计。
                - `"tools"`: 工具定义区块统计。
                - `"history"`: 历史对话上下文区块统计。
                每个区块字典包含以下子字段：
                - `"name"` (str): 区块中文可读名称（如 `"系统提示词"`, `"工具"`, `"历史对话"`）。
                - `"available"` (bool): 该区块统计数据是否有效（若未捕获真实请求，仅历史对话有效）。
                - `"tokens"` (int | None): 该区块消耗的 Token 数量，不可用时为 `None`。
                - `"bytes"` (None): 预留的字节级统计字段，当前固定为 `None`。
                - `"ratio_of_used"` (float | None): 该区块占已消耗 Token 总量的百分比。
                - `"ratio_of_max"` (float | None): 该区块占最大容量上限的百分比。
                - `"description"` (str): 该区块数据的采集状态与计算说明。
            - `summary_text` (str): 人类可读的用量统计来源与可信度描述摘要。

        Raises:
            本函数内部采用完全容错与边界保护设计，不主动抛出业务异常。
        """
        captured = request_messages is not None
        # 按区块归类输入消息（系统提示词、工具定义、历史上下文）
        groups: dict[str, list[Any]] = {name: [] for name in ("system_prompt", "tools", "history")}
        for message in request_messages if request_messages is not None else messages:
            groups["system_prompt" if message.type == "system" else "history"].append(message)
        known = {
            name: count_tokens_approximately(items) if items else 0
            for name, items in groups.items()
        }
        known["tools"] = count_tokens_approximately([], tools=tools) if tools else 0
        estimated = sum(known.values())
        used = reported_tokens if reported_tokens is not None else estimated

        # 若模型返回了实际 Token 用量，按内容占比将总量精确拆分至各个区块
        if reported_tokens is not None and estimated:
            cumulative = 0
            allocated = 0
            for name, value in known.items():
                cumulative += value
                boundary = round(cumulative * used / estimated)
                known[name], allocated = boundary - allocated, boundary
        labels = {"system_prompt": "系统提示词", "tools": "工具", "history": "历史对话"}
        sections = {
            name: {
                "name": labels[name],
                "available": captured or name == "history",
                "tokens": value if captured or name == "history" else None,
                "bytes": None,
                "ratio_of_used": (value / used if used else 0)
                if captured or name == "history"
                else None,
                "ratio_of_max": value / maximum if captured or name == "history" else None,
                "description": "按实际请求内容估算构成" if captured else "尚未采集实际模型请求",
            }
            for name, value in known.items()
        }
        remaining = max(0, maximum - used)
        summary = "尚未采集实际模型请求；当前仅估算历史消息，其他区块未统计。"
        if reported_tokens is not None:
            summary = "最近一次主 Agent 请求及其输出；总量由模型上报，各项按内容比例估算。"
        elif captured:
            summary = "最近一次主 Agent 实际请求及其输出的内容估算；模型未返回用量。"
        return {
            "max_tokens": maximum,
            "used_tokens": used,
            "remaining_tokens": remaining,
            "used_ratio": used / maximum,
            "remaining_ratio": remaining / maximum,
            "sections": sections,
            "summary_text": summary,
        }
