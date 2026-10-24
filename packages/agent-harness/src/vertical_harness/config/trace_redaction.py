"""LangSmith 上传策略：只保留关联、模型标识和数字用量。"""

from __future__ import annotations

from typing import Any


class TraceRedaction:
    """不上传正文或原始异常；保留官方成本计算需要的 Token 数据。"""

    @staticmethod
    def anonymize(payload: dict[str, Any]) -> dict[str, Any]:
        """过滤 SDK 传入的 outputs、metadata 或 error，不修改运行中的对象。

        :param payload: LangSmith SDK 已转为 JSON 的待上传数据。
        :return: 仅含白名单字段的数据。
        """
        if "error" in payload:
            return {"error": "Error details omitted"}
        safe = {
            key: payload[key]
            for key in (
                "thread_id",
                "desktop_run_id",
                "ls_model_name",
                "ls_provider",
                "ls_model_type",
                "lc_agent_name",
                "langgraph_node",
                "langgraph_step",
                "langgraph_checkpoint_ns",
                "revision_id",
            )
            if key in payload and isinstance(payload[key], (str, int))
        }
        usage: Any = {}
        llm_output = payload.get("llm_output")
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage")
        generations = payload.get("generations")
        if isinstance(generations, list) and generations:
            batch = generations[0]
            if isinstance(batch, list) and batch and isinstance(batch[0], dict):
                message = batch[0].get("message")
                if isinstance(message, dict):
                    message = message.get("kwargs", message)
                    details = message.get("usage_metadata") if isinstance(message, dict) else None
                    if isinstance(details, dict):
                        usage = {
                            "prompt_tokens": details.get("input_tokens"),
                            "completion_tokens": details.get("output_tokens"),
                            "total_tokens": details.get("total_tokens"),
                        }
                        for source, target, source_key, target_key in (
                            (
                                "input_token_details",
                                "prompt_tokens_details",
                                "cache_read",
                                "cached_tokens",
                            ),
                            (
                                "output_token_details",
                                "completion_tokens_details",
                                "reasoning",
                                "reasoning_tokens",
                            ),
                        ):
                            breakdown = details.get(source)
                            if isinstance(breakdown, dict):
                                usage[target] = {target_key: breakdown.get(source_key)}
        if not isinstance(usage, dict):
            return safe
        tokens = {
            key: usage[key]
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage.get(key), int)
            and not isinstance(usage[key], bool)
            and usage[key] >= 0
        }
        for section, key in (
            ("prompt_tokens_details", "cached_tokens"),
            ("completion_tokens_details", "reasoning_tokens"),
        ):
            breakdown = usage.get(section)
            value = breakdown.get(key) if isinstance(breakdown, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                tokens[section] = {key: value}
        if tokens:
            safe["llm_output"] = {"token_usage": tokens}
        return safe
