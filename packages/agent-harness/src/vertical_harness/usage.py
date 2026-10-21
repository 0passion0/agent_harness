from __future__ import annotations

from typing import Any

from langchain_core.messages.utils import count_tokens_approximately


class UsageEstimator:
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

        :param messages: 原始对话消息列表。
        :type messages: list[typing.Any]
        :param model: 模型标识符字符串。
        :type model: str
        :param maximum: 上下文预算上限，默认 475,000。
        :type maximum: int
        :param options: 可选的桌面配置选项。
        :type options: dict[str, typing.Any] | None
        :param request_messages: 实际发送给模型的完整请求消息列表（包含系统消息）。
        :type request_messages: list[typing.Any] | None
        :param tools: 绑定至模型的工具列表。
        :type tools: list[typing.Any] | None
        :param reported_tokens: 模型 API 返回的真实总 Token 消耗量。
        :type reported_tokens: int | None
        :return: 包含 max_tokens、used_tokens、remaining_tokens、sections 及 summary_text 的字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21

        .. code-block:: python

            # return 结构契约：
            {
                "max_tokens": 475000,
                "used_tokens": 1250,
                "remaining_tokens": 473750,
                "used_ratio": 0.0026,
                "remaining_ratio": 0.9974,
                "sections": {
                    "system_prompt": {"name": "系统提示词", "tokens": 400, ...},
                    "tools": {"name": "工具", "tokens": 600, ...},
                    "history": {"name": "历史对话", "tokens": 250, ...}
                },
                "summary_text": "..."
            }
        """
        # 第一步：根据是否捕获实际请求消息将消息分组为 system_prompt 与 history
        captured = request_messages is not None
        groups: dict[str, list[Any]] = {name: [] for name in ("system_prompt", "tools", "history")}
        for message in request_messages if captured else messages:
            groups["system_prompt" if message.type == "system" else "history"].append(message)

        # 第二步：基于分组消息与工具计算各区块的近似 Token 估算值
        known = {
            name: count_tokens_approximately(items) if items else 0 for name, items in groups.items()
        }
        known["tools"] = count_tokens_approximately([], tools=tools) if tools else 0
        estimated = sum(known.values())

        # 第三步：若模型返回了权威用量指标，按各区块估算比例进行等比归一化分配
        used = reported_tokens if reported_tokens is not None else estimated
        if reported_tokens is not None and estimated:
            cumulative = 0
            allocated = 0
            for name, value in known.items():
                cumulative += value
                boundary = round(cumulative * used / estimated)
                known[name], allocated = boundary - allocated, boundary

        # 第四步：格式化输出 sections 各分块统计明细
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

        # 第五步：生成摘要描述文本并返回统计结果
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
