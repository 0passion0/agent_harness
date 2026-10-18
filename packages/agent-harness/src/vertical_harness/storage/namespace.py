"""LangGraph Store 多租户隔离命名空间派生与显式迁移模块。"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.runtime import Runtime
    from langgraph.store.base import BaseStore

    from vertical_harness.plugins.contracts import RunContext

logger = logging.getLogger(__name__)


def _identity_segment(value: str | None) -> str:
    """计算单个租户/用户/存储桶标识符的确定性 SHA-256 摘要，避免字符碰撞。

    :param value: 原始标识符字符串。
    :return: 64位十六进制哈希字符串。
    :raises ValueError: 当标识符缺失或为空白时抛出。
    """
    logger.debug("calculating_identity_segment")
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
    logger.debug("deriving_tenant_memory_namespace", extra={"event_fields": {"bucket": bucket}})
    context = getattr(runtime, "context", None) if runtime is not None else None
    ns = (
        "file_memory_v2",
        _identity_segment(getattr(context, "tenant_id", None)),
        _identity_segment(getattr(context, "user_id", None)),
        _identity_segment(bucket),
    )
    logger.debug("tenant_memory_namespace_derived")
    return ns


def copy_memory_namespace(
    store: BaseStore,
    source: tuple[str, ...],
    destination: tuple[str, ...],
) -> int:
    """显式迁移旧版命名空间记录至新命名空间，不覆盖或删除现有数据。

    调用方必须预先核实记录归属所有权：旧版清洗过的 ID 可能存在碰撞。
    在现有记录数据一致的前提下，重复执行部分迁移是安全且幂等的。

    :param store: 调用方持有的同步 BaseStore 存储实例。
    :param source: 已经过所有权验证的源命名空间元组。
    :param destination: 目标命名空间元组。
    :return: 成功复制的记录总数。
    :raises ValueError: 当源与目标命名空间相同、为空或目标命名空间存在冲突键时抛出。
    """
    logger.info("memory_namespace_copy_started")
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
    logger.info("memory_namespace_copy_completed", extra={"event_fields": {"copied_count": count}})
    return count


__all__ = [
    "copy_memory_namespace",
    "tenant_memory_namespace",
]

