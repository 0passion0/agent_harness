"""智能体与 LangGraph 工作流 Time Travel（时间旅行回溯）、Checkpoint Fork（状态分叉）及同步持久化执行模块。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Durability, StateSnapshot

from vertical_harness.plugins.contracts import StrictModel

logger = logging.getLogger(__name__)

# 默认强制采用同步检查点持久化，确保每个节点步骤完成后检查点立即落盘，支撑精准 Time Travel 与 Fork
DEFAULT_DURABILITY: Durability = "sync"


class CheckpointSummary(StrictModel):
    """单个 LangGraph 检查点快照的安全脱敏元数据摘要模型（不含对话原文或敏感载荷）。"""

    # 所属会话线程标识符
    thread_id: str
    # 检查点唯一标识符
    checkpoint_id: str
    # 子图检查点命名空间（主图为 ''）
    checkpoint_ns: str = ""
    # 父检查点唯一标识符（首步前为 None）
    parent_checkpoint_id: str | None = None
    # 执行步序号（如 -1, 0, 1, ...）
    step: int | None = None
    # 检查点来源（如 'input', 'loop', 'update', 'fork'）
    source: str | None = None
    # 当前检查点之后待执行的节点名称元组（已完成时为空元组）
    next_nodes: tuple[str, ...] = ()
    # 检查点创建时间戳（ISO-8601 字符串）
    created_at: str | None = None
    # 当前状态中包含的消息数量
    message_count: int = 0
    # 当前状态字典中包含的顶层键名集合
    state_keys: tuple[str, ...] = ()
    # 当前检查点是否处于挂起等待恢复（interrupt）状态
    interrupted: bool = False

    @classmethod
    def from_snapshot(cls, snapshot: StateSnapshot) -> CheckpointSummary:
        """从 LangGraph 原生 `StateSnapshot` 提取脱敏的检查点摘要。

        :param snapshot: LangGraph 状态快照对象。
        :return: 结构化的 CheckpointSummary 实例。
        """
        configurable = snapshot.config.get("configurable", {}) if snapshot.config else {}
        parent_cfg = (
            snapshot.parent_config.get("configurable", {}) if snapshot.parent_config else {}
        )
        metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
        values = snapshot.values if isinstance(snapshot.values, dict) else {}
        messages = values.get("messages")
        msg_count = len(messages) if isinstance(messages, list) else 0
        raw_step = metadata.get("step")
        raw_source = metadata.get("source")
        return cls(
            thread_id=str(configurable.get("thread_id", "")),
            checkpoint_id=str(configurable.get("checkpoint_id", "")),
            checkpoint_ns=str(configurable.get("checkpoint_ns", "")),
            parent_checkpoint_id=(
                str(parent_cfg["checkpoint_id"]) if parent_cfg.get("checkpoint_id") else None
            ),
            step=int(raw_step) if isinstance(raw_step, int) else None,
            source=str(raw_source) if raw_source is not None else None,
            next_nodes=tuple(str(n) for n in (snapshot.next or ())),
            created_at=str(snapshot.created_at) if snapshot.created_at is not None else None,
            message_count=msg_count,
            state_keys=tuple(sorted(str(k) for k in values)),
            interrupted=bool(snapshot.interrupts),
        )


def make_checkpoint_config(
    thread_id: str,
    checkpoint_id: str | None = None,
    *,
    checkpoint_ns: str = "",
    callbacks: Sequence[Any] | None = None,
) -> RunnableConfig:
    """构造用于状态查询、Time Travel 回放或 Fork 的 `RunnableConfig`。

    :param thread_id: 目标线程标识符。
    :param checkpoint_id: 可选的历史检查点标识符。
    :param checkpoint_ns: 可选的子图检查点命名空间，默认为主图 ''。
    :param callbacks: 可选的回调处理器序列。
    :return: 标准 LangChain RunnableConfig 字典。
    """
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "checkpoint_ns": checkpoint_ns,
    }
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    config: RunnableConfig = {"configurable": configurable}
    if callbacks is not None:
        config["callbacks"] = list(callbacks)
    return config


def get_checkpoint_history(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    *,
    checkpoint_ns: str = "",
    limit: int | None = None,
) -> list[CheckpointSummary]:
    """查询指定线程的 Time Travel 检查点历史列表（按时间倒序，最新在前）。

    :param graph: 已绑定 Checkpointer 的编译状态图。
    :param thread_id: 线程标识符。
    :param checkpoint_ns: 子图检查点命名空间，默认 ''。
    :param limit: 可选返回的最大检查点条数。
    :return: CheckpointSummary 列表。
    """
    config = make_checkpoint_config(thread_id, checkpoint_ns=checkpoint_ns)
    snapshots = list(graph.get_state_history(config, limit=limit))
    summaries = [CheckpointSummary.from_snapshot(snap) for snap in snapshots]
    logger.debug(
        "time_travel_history_loaded",
        extra={"event_fields": {"checkpoint_count": len(summaries)}},
    )
    return summaries


async def aget_checkpoint_history(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    *,
    checkpoint_ns: str = "",
    limit: int | None = None,
) -> list[CheckpointSummary]:
    """异步查询指定线程的 Time Travel 检查点历史列表（按时间倒序，最新在前）。"""
    config = make_checkpoint_config(thread_id, checkpoint_ns=checkpoint_ns)
    summaries: list[CheckpointSummary] = []
    async for snap in graph.aget_state_history(config, limit=limit):
        summaries.append(CheckpointSummary.from_snapshot(snap))
    logger.debug(
        "time_travel_history_loaded",
        extra={"event_fields": {"checkpoint_count": len(summaries)}},
    )
    return summaries


def get_checkpoint_snapshot(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    checkpoint_id: str | None = None,
    *,
    checkpoint_ns: str = "",
    subgraphs: bool = False,
) -> StateSnapshot:
    """获取指定线程及可选历史 `checkpoint_id` 的完整状态快照。

    :param graph: 已绑定 Checkpointer 的编译状态图。
    :param thread_id: 线程标识符。
    :param checkpoint_id: 可选的历史检查点 ID；为 None 时获取最新检查点。
    :param checkpoint_ns: 检查点命名空间。
    :param subgraphs: 是否同时拉取嵌套子图状态。
    :return: 匹配的 StateSnapshot。
    :raises ValueError: 当指定的线程或历史检查点不存在时抛出。
    """
    config = make_checkpoint_config(thread_id, checkpoint_id, checkpoint_ns=checkpoint_ns)
    snapshot = graph.get_state(config, subgraphs=subgraphs)
    if not snapshot.values and not snapshot.next and snapshot.created_at is None:
        raise ValueError(
            f"Checkpoint not found for thread_id={thread_id!r}, checkpoint_id={checkpoint_id!r}"
        )
    return snapshot


async def aget_checkpoint_snapshot(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    checkpoint_id: str | None = None,
    *,
    checkpoint_ns: str = "",
    subgraphs: bool = False,
) -> StateSnapshot:
    """异步获取指定线程及可选历史 `checkpoint_id` 的完整状态快照。"""
    config = make_checkpoint_config(thread_id, checkpoint_id, checkpoint_ns=checkpoint_ns)
    snapshot = await graph.aget_state(config, subgraphs=subgraphs)
    if not snapshot.values and not snapshot.next and snapshot.created_at is None:
        raise ValueError(
            f"Checkpoint not found for thread_id={thread_id!r}, checkpoint_id={checkpoint_id!r}"
        )
    return snapshot


def fork_checkpoint(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    checkpoint_id: str | None = None,
    *,
    values: dict[str, Any] | None = None,
    as_node: str | None = None,
    fork_thread_id: str | None = None,
    new_thread_id: str | None = None,
    checkpoint_ns: str = "",
) -> RunnableConfig:
    """从指定历史检查点执行 Time Travel Fork（同线程状态分叉或跨线程克隆分叉）。

    支持两种分叉模式：
    1. **同线程分叉**（`fork_thread_id=None` 或与 `thread_id` 相同）：基于历史 `checkpoint_id`
       调用 `graph.update_state` 生成新的分叉检查点（保留 `next` 节点并在该点覆盖/追加 `values`）。
    2. **跨线程分叉**（指定新的 `fork_thread_id` / `new_thread_id`）：将源线程在 `checkpoint_id` 处的底层检查点元组
       完整克隆至新线程（不污染原线程历史），并可选应用 `values` 状态修改。

    :param graph: 已绑定 Checkpointer 的编译状态图。
    :param thread_id: 源线程标识符。
    :param checkpoint_id: 要回溯的目标历史检查点 ID；为 None 时默认取源线程最新检查点。
    :param values: 可选在分叉点注入或覆盖的状态增量字典。
    :param as_node: 可选指定 `update_state` 伪装的来源节点名称。
    :param fork_thread_id: 可选的新分叉线程标识符；指定时克隆检查点到新线程。
    :param new_thread_id: `fork_thread_id` 的同义参数别名。
    :param checkpoint_ns: 检查点命名空间，默认 ''。
    :return: 指向新生成分叉检查点的 `RunnableConfig`。
    :raises ValueError: 当源检查点不存在或图未配置 Checkpointer 时抛出。
    """
    if not isinstance(graph.checkpointer, object) or graph.checkpointer in (None, False):
        raise ValueError("Graph must be compiled with a checkpointer to support Time Travel Fork")

    target_fork_thread = fork_thread_id or new_thread_id
    snapshot = get_checkpoint_snapshot(graph, thread_id, checkpoint_id, checkpoint_ns=checkpoint_ns)
    source_cfg = snapshot.config
    source_cid = source_cfg.get("configurable", {}).get("checkpoint_id", "")
    effective_as_node = (
        as_node if as_node is not None else ("__start__" if not snapshot.next else None)
    )

    if target_fork_thread is not None and target_fork_thread != thread_id:
        tup = graph.checkpointer.get_tuple(source_cfg)
        if tup is None:
            raise ValueError(f"Checkpoint tuple not found for checkpoint_id={source_cid!r}")
        target_base: RunnableConfig = {
            "configurable": {
                "thread_id": target_fork_thread,
                "checkpoint_ns": checkpoint_ns,
            }
        }
        if (
            checkpoint_ns
            or any(task.state is not None for task in snapshot.tasks)
            or any(graph.get_subgraphs())
        ):
            raise ValueError(
                "Cross-thread fork does not support nested subgraph checkpoints; use a same-thread fork"
            )
        if graph.checkpointer.get_tuple(target_base) is not None:
            raise ValueError("Fork destination thread already exists")
        cloned_metadata = deepcopy(tup.metadata) if tup.metadata else {"source": "fork", "step": 0}
        cloned_cfg = graph.checkpointer.put(
            target_base,
            deepcopy(tup.checkpoint),
            cloned_metadata,
            dict(tup.checkpoint.get("channel_versions", {})),
        )
        pending: dict[str, list[tuple[str, Any]]] = {}
        for task_id, channel, value in tup.pending_writes or ():
            pending.setdefault(task_id, []).append((channel, deepcopy(value)))
        for task_id, writes in pending.items():
            graph.checkpointer.put_writes(cloned_cfg, writes, task_id)
        if values is not None:
            forked_cfg = graph.update_state(cloned_cfg, values, as_node=effective_as_node)
        else:
            forked_cfg = cloned_cfg
    else:
        forked_cfg = graph.update_state(source_cfg, values, as_node=effective_as_node)

    fork_cid = forked_cfg.get("configurable", {}).get("checkpoint_id", "")
    logger.info(
        "time_travel_fork_created",
        extra={
            "event_fields": {
                "source_checkpoint_id": str(source_cid),
                "fork_checkpoint_id": str(fork_cid),
                "cross_thread": bool(target_fork_thread and target_fork_thread != thread_id),
            }
        },
    )
    return forked_cfg


async def afork_checkpoint(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    checkpoint_id: str | None = None,
    *,
    values: dict[str, Any] | None = None,
    as_node: str | None = None,
    fork_thread_id: str | None = None,
    new_thread_id: str | None = None,
    checkpoint_ns: str = "",
) -> RunnableConfig:
    """异步从指定历史检查点执行 Time Travel Fork（同线程状态分叉或跨线程克隆分叉）。"""
    if not isinstance(graph.checkpointer, object) or graph.checkpointer in (None, False):
        raise ValueError("Graph must be compiled with a checkpointer to support Time Travel Fork")

    target_fork_thread = fork_thread_id or new_thread_id
    snapshot = await aget_checkpoint_snapshot(
        graph, thread_id, checkpoint_id, checkpoint_ns=checkpoint_ns
    )
    source_cfg = snapshot.config
    source_cid = source_cfg.get("configurable", {}).get("checkpoint_id", "")
    effective_as_node = (
        as_node if as_node is not None else ("__start__" if not snapshot.next else None)
    )

    if target_fork_thread is not None and target_fork_thread != thread_id:
        tup = await graph.checkpointer.aget_tuple(source_cfg)
        if tup is None:
            raise ValueError(f"Checkpoint tuple not found for checkpoint_id={source_cid!r}")
        target_base: RunnableConfig = {
            "configurable": {
                "thread_id": target_fork_thread,
                "checkpoint_ns": checkpoint_ns,
            }
        }
        if (
            checkpoint_ns
            or any(task.state is not None for task in snapshot.tasks)
            or any(graph.get_subgraphs())
        ):
            raise ValueError(
                "Cross-thread fork does not support nested subgraph checkpoints; use a same-thread fork"
            )
        if await graph.checkpointer.aget_tuple(target_base) is not None:
            raise ValueError("Fork destination thread already exists")
        cloned_metadata = deepcopy(tup.metadata) if tup.metadata else {"source": "fork", "step": 0}
        cloned_cfg = await graph.checkpointer.aput(
            target_base,
            deepcopy(tup.checkpoint),
            cloned_metadata,
            dict(tup.checkpoint.get("channel_versions", {})),
        )
        pending: dict[str, list[tuple[str, Any]]] = {}
        for task_id, channel, value in tup.pending_writes or ():
            pending.setdefault(task_id, []).append((channel, deepcopy(value)))
        for task_id, writes in pending.items():
            await graph.checkpointer.aput_writes(cloned_cfg, writes, task_id)
        if values is not None:
            forked_cfg = await graph.aupdate_state(cloned_cfg, values, as_node=effective_as_node)
        else:
            forked_cfg = cloned_cfg
    else:
        forked_cfg = await graph.aupdate_state(source_cfg, values, as_node=effective_as_node)

    fork_cid = forked_cfg.get("configurable", {}).get("checkpoint_id", "")
    logger.info(
        "time_travel_fork_created",
        extra={
            "event_fields": {
                "source_checkpoint_id": str(source_cid),
                "fork_checkpoint_id": str(fork_cid),
                "cross_thread": bool(target_fork_thread and target_fork_thread != thread_id),
            }
        },
    )
    return forked_cfg


def _resolve_invoke_args(
    thread_or_input: str | dict[str, Any] | Command[Any] | None,
    thread_id: str | None,
    input_state: dict[str, Any] | Command[Any] | None,
) -> tuple[str, dict[str, Any] | Command[Any] | None]:
    """解析 `time_travel_invoke` 的位置与关键字参数，同时支持 (graph, thread_id) 与 (graph, inputs, *, thread_id=...)。"""
    if isinstance(thread_or_input, str) and thread_id is None:
        return thread_or_input, input_state
    if thread_id is not None:
        resolved_input = input_state if input_state is not None else thread_or_input  # type: ignore[assignment]
        return thread_id, resolved_input
    raise ValueError("time_travel_invoke requires 'thread_id'")


def time_travel_invoke(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_or_input: str | dict[str, Any] | Command[Any] | None = None,
    *,
    thread_id: str | None = None,
    checkpoint_id: str | None = None,
    prompt: str | None = None,
    input_state: dict[str, Any] | Command[Any] | None = None,
    values: dict[str, Any] | None = None,
    as_node: str | None = None,
    fork_thread_id: str | None = None,
    new_thread_id: str | None = None,
    checkpoint_ns: str = "",
    context: Any = None,
    callbacks: Sequence[Any] | None = None,
    run_id: Any = None,
    durability: Durability = DEFAULT_DURABILITY,
) -> dict[str, Any] | Any:
    """结合 `durability="sync"` 执行智能体或工作流的 Time Travel 回放与 Time Travel Fork 重跑。

    - 若指定了 `values` 或 `fork_thread_id` / `new_thread_id`，先调用 `fork_checkpoint` 创建分叉检查点，再从分叉点继续运行；
    - 若仅指定 `checkpoint_id`（无 `values` 与 `fork_thread_id`），直接从该历史检查点回放（`input=None`）
      或注入新指令（`prompt` / `input_state`）分叉执行。
    """
    resolved_thread_id, resolved_input = _resolve_invoke_args(
        thread_or_input, thread_id, input_state
    )
    target_fork_thread = fork_thread_id or new_thread_id
    should_fork = values is not None or (
        target_fork_thread is not None and target_fork_thread != resolved_thread_id
    )
    if should_fork:
        run_config = fork_checkpoint(
            graph,
            resolved_thread_id,
            checkpoint_id,
            values=values,
            as_node=as_node,
            fork_thread_id=target_fork_thread,
            checkpoint_ns=checkpoint_ns,
        )
    elif checkpoint_id is not None:
        snapshot = get_checkpoint_snapshot(
            graph, resolved_thread_id, checkpoint_id, checkpoint_ns=checkpoint_ns
        )
        run_config = deepcopy(snapshot.config)
    else:
        run_config = make_checkpoint_config(
            resolved_thread_id, checkpoint_id=None, checkpoint_ns=checkpoint_ns
        )

    if callbacks is not None:
        run_config = {**run_config, "callbacks": list(callbacks)}
    if run_id is not None:
        run_config = {**run_config, "run_id": run_id}

    payload: dict[str, Any] | Command[Any] | None
    if resolved_input is not None:
        payload = resolved_input
    elif prompt is not None:
        payload = {"messages": [HumanMessage(content=prompt)]}
    else:
        payload = None

    logger.info(
        "time_travel_invoke_started",
        extra={
            "event_fields": {
                "forked": should_fork,
                "has_input": payload is not None,
                "durability": durability,
            }
        },
    )
    return graph.invoke(
        payload,
        config=run_config,
        context=context,
        durability=durability,
    )


async def atime_travel_invoke(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_or_input: str | dict[str, Any] | Command[Any] | None = None,
    *,
    thread_id: str | None = None,
    checkpoint_id: str | None = None,
    prompt: str | None = None,
    input_state: dict[str, Any] | Command[Any] | None = None,
    values: dict[str, Any] | None = None,
    as_node: str | None = None,
    fork_thread_id: str | None = None,
    new_thread_id: str | None = None,
    checkpoint_ns: str = "",
    context: Any = None,
    callbacks: Sequence[Any] | None = None,
    run_id: Any = None,
    durability: Durability = DEFAULT_DURABILITY,
) -> dict[str, Any] | Any:
    """结合 `durability="sync"` 异步执行智能体或工作流的 Time Travel 回放与 Time Travel Fork 重跑。"""
    resolved_thread_id, resolved_input = _resolve_invoke_args(
        thread_or_input, thread_id, input_state
    )
    target_fork_thread = fork_thread_id or new_thread_id
    should_fork = values is not None or (
        target_fork_thread is not None and target_fork_thread != resolved_thread_id
    )
    if should_fork:
        run_config = await afork_checkpoint(
            graph,
            resolved_thread_id,
            checkpoint_id,
            values=values,
            as_node=as_node,
            fork_thread_id=target_fork_thread,
            checkpoint_ns=checkpoint_ns,
        )
    elif checkpoint_id is not None:
        snapshot = await aget_checkpoint_snapshot(
            graph, resolved_thread_id, checkpoint_id, checkpoint_ns=checkpoint_ns
        )
        run_config = deepcopy(snapshot.config)
    else:
        run_config = make_checkpoint_config(
            resolved_thread_id, checkpoint_id=None, checkpoint_ns=checkpoint_ns
        )

    if callbacks is not None:
        run_config = {**run_config, "callbacks": list(callbacks)}
    if run_id is not None:
        run_config = {**run_config, "run_id": run_id}

    payload: dict[str, Any] | Command[Any] | None
    if resolved_input is not None:
        payload = resolved_input
    elif prompt is not None:
        payload = {"messages": [HumanMessage(content=prompt)]}
    else:
        payload = None

    logger.info(
        "time_travel_invoke_started",
        extra={
            "event_fields": {
                "forked": should_fork,
                "has_input": payload is not None,
                "durability": durability,
            }
        },
    )
    return await graph.ainvoke(
        payload,
        config=run_config,
        context=context,
        durability=durability,
    )


__all__ = [
    "DEFAULT_DURABILITY",
    "CheckpointSummary",
    "afork_checkpoint",
    "aget_checkpoint_history",
    "aget_checkpoint_snapshot",
    "atime_travel_invoke",
    "fork_checkpoint",
    "get_checkpoint_history",
    "get_checkpoint_snapshot",
    "make_checkpoint_config",
    "time_travel_invoke",
]
