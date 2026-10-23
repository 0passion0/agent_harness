"""模型上下文 Token 估算、按区块拆分统计与用量报告生成模块。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages.utils import count_tokens_approximately


class UsageEstimator:
    """Token 上下文用量估算器，支持系统提示词、工具定义与历史消息的多维度拆分。"""

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
        """估算或按模型真实上报用量拆分各区块（系统提示词、工具定义、历史消息）的 Token 构成与占比。"""
        captured = request_messages is not None
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
