"""LangGraph Store 多租户隔离命名空间派生与分段清洗模块。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from vertical_harness.plugins.contracts import RunContext

# LangGraph 原生 SqliteStore 使用 '.' 拼接命名空间分段，因此分段内部严禁出现 '.' 字符；
# 此处正则匹配除英文字母、数字、减号、下划线、@、+、:、~ 之外的所有不安全字符。
_UNSAFE_NS_CHARS = re.compile(r"[^A-Za-z0-9\-_@+:~]")


def _sanitize_segment(value: str | None, fallback: str = "default") -> str:
    """将运行时标识符规范化为 LangGraph 与 StoreBackend 安全合法的命名空间标签。

    :param value: 待清洗的原始命名空间分段字符串，可能为 None 或包含特殊字符。
    :param fallback: 当输入为 None 或清洗后为空字符串时的回退默认值。
    :return: 仅包含安全字符的规范化命名空间分段字符串。
    """
    if not value:
        return fallback
    cleaned = _UNSAFE_NS_CHARS.sub("_", value.strip())
    return cleaned or fallback


def tenant_memory_namespace(
    runtime: Runtime[RunContext] | None = None,
    *,
    bucket: str = "memories",
) -> tuple[str, ...]:
    """从 LangGraph Runtime 运行时上下文中派生出租户和用户隔离的命名空间元组。

    :param runtime: LangGraph 运行时对象，其 context 属性包含当前租户与用户标识。
    :param bucket: 存储分类桶标识，默认为 'memories'。
    :return: 由清洗后的 (tenant_id, user_id, bucket) 组成的隔离命名空间三元组。
    """
    context = getattr(runtime, "context", None) if runtime is not None else None
    return (
        _sanitize_segment(getattr(context, "tenant_id", None), "default"),
        _sanitize_segment(getattr(context, "user_id", None), "default"),
        _sanitize_segment(bucket, "memories"),
    )


__all__ = [
    "tenant_memory_namespace",
]
