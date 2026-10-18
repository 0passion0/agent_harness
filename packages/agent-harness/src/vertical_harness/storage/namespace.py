"""LangGraph Store 多租户隔离命名空间派生与显式迁移模块。"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.runtime import Runtime
    from langgraph.store.base import BaseStore

    from vertical_harness.plugins.contracts import RunContext


def _identity_segment(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Memory namespaces require nonempty trusted tenant/user/bucket IDs")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tenant_memory_namespace(
    runtime: Runtime[RunContext] | None = None,
    *,
    bucket: str = "memories",
) -> tuple[str, ...]:
    """从 LangGraph Runtime 运行时上下文中派生出租户和用户隔离的命名空间元组。

    :param runtime: LangGraph 运行时对象，其 context 属性包含当前租户与用户标识。
    :param bucket: 存储分类桶标识，默认为 'memories'。
    :return: 版本标识及未经清洗的身份摘要，不回退到共享默认身份。
    :raises ValueError: 当身份缺失或为空时抛出。
    """
    context = getattr(runtime, "context", None) if runtime is not None else None
    return (
        "file_memory_v2",
        _identity_segment(getattr(context, "tenant_id", None)),
        _identity_segment(getattr(context, "user_id", None)),
        _identity_segment(bucket),
    )


def copy_memory_namespace(
    store: BaseStore,
    source: tuple[str, ...],
    destination: tuple[str, ...],
) -> int:
    """Copy explicitly attributed legacy records without deleting or overwriting data.

    The operator must verify ownership first: legacy sanitized IDs can collide.
    Repeating a partial copy is safe when existing values match.

    :param store: Caller-owned synchronous store.
    :param source: Exact namespace whose ownership has been verified.
    :param destination: Explicit new namespace.
    :return: Number of copied records.
    :raises ValueError: If namespaces match or destination data conflicts.
    """
    if (
        not source
        or not destination
        or source[: len(destination)] == destination
        or destination[: len(source)] == source
    ):
        raise ValueError("Migration requires distinct, nonempty namespaces")
    count = offset = 0
    while items := store.search(source, limit=100, offset=offset):
        for item in items:
            if item.namespace != source:
                continue
            existing = store.get(destination, item.key)
            if existing is not None and existing.value != item.value:
                raise ValueError(f"Destination contains conflicting memory key: {item.key}")
            store.put(destination, item.key, item.value)
            count += 1
        offset += len(items)
    return count


__all__ = [
    "copy_memory_namespace",
    "tenant_memory_namespace",
]
