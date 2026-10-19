"""科研数据分析 LangGraph 工作流的节点控制与状态操作 Hook 模块。

本模块为主智能体（Supervisor）提供对固定流程节点的精细化控制能力：
- 重试某个节点（`retry_node`）；
- 修改/纠正节点状态（`modify_node_state`）；
- 基于指定节点分叉新建分支（`fork_node_branch`）；
- 审查各节点执行历史与检查点（`inspect_node_checkpoints`）。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from vertical_harness.engine.time_travel import (
    CheckpointSummary,
    fork_checkpoint,
    get_checkpoint_history,
    get_checkpoint_snapshot,
    time_travel_invoke,
)

logger = logging.getLogger(__name__)


def inspect_node_checkpoints(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    *,
    limit: int | None = None,
) -> list[CheckpointSummary]:
    """查询工作流在各节点产生的检查点历史，供主智能体监控排查。

    :param graph: 绑定了持久化 Checkpointer 的已编译工作流图。
    :type graph: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
    :param thread_id: 当前分析会话的线程标识符。
    :type thread_id: str
    :param limit: 可选的最大检查点查询返回条数。
    :type limit: int | None
    :return: 按时间倒序排列的检查点摘要列表。
    :rtype: list[vertical_harness.engine.time_travel.CheckpointSummary]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：记录检查点查询调试日志
    logger.debug(
        "inspect_node_checkpoints_called",
        extra={"event_fields": {"thread_id": thread_id, "limit": limit}},
    )
    # 第二步：调用 time-travel 引擎拉取历史快照
    return get_checkpoint_history(graph, thread_id, limit=limit)


def modify_node_state(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    values: dict[str, Any],
    *,
    as_node: str | None = None,
    checkpoint_id: str | None = None,
) -> RunnableConfig:
    """Hook：修改或纠正指定节点的状态变量，为重试或下游节点提供修正后的参数。

    :param graph: 绑定了持久化 Checkpointer 的已编译工作流图。
    :type graph: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
    :param thread_id: 目标工作流线程标识符。
    :type thread_id: str
    :param values: 待覆盖或追加的状态增量字典。
    :type values: dict[str, typing.Any]
    :param as_node: 可选伪装的来源节点名称（如 'parse_and_validate_input'）。
    :type as_node: str | None
    :param checkpoint_id: 可选的目标历史检查点 ID；默认使用最新检查点。
    :type checkpoint_id: str | None
    :return: 指向状态更新后新检查点的 RunnableConfig。
    :rtype: langchain_core.runnables.RunnableConfig
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "modify_node_state_called",
        extra={
            "event_fields": {
                "thread_id": thread_id,
                "as_node": as_node,
                "checkpoint_id": checkpoint_id,
                "keys": list(values.keys()),
            }
        },
    )
    # 第一步：获取基准检查点快照配置
    snapshot = get_checkpoint_snapshot(graph, thread_id, checkpoint_id)
    source_cfg = snapshot.config

    # 第二步：调用 graph.update_state 更新节点状态
    updated_cfg = graph.update_state(source_cfg, values, as_node=as_node)
    logger.info("modify_node_state_completed")
    return updated_cfg


def retry_node(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    *,
    checkpoint_id: str | None = None,
    values: dict[str, Any] | None = None,
    as_node: str | None = None,
) -> dict[str, Any] | Any:
    """Hook：重试执行某个节点或从指定历史检查点恢复流转。

    :param graph: 绑定了持久化 Checkpointer 的已编译工作流图。
    :type graph: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
    :param thread_id: 目标工作流线程标识符。
    :type thread_id: str
    :param checkpoint_id: 发生异常或需要重试的目标检查点 ID；默认为最新检查点。
    :type checkpoint_id: str | None
    :param values: 可选在重试前注入的状态修复字典。
    :type values: dict[str, typing.Any] | None
    :param as_node: 可选的伪装更新来源节点。
    :type as_node: str | None
    :return: 重试执行后的工作流最终状态输出字典。
    :rtype: dict[str, typing.Any] | typing.Any
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "retry_node_invoked",
        extra={
            "event_fields": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
                "has_values": values is not None,
            }
        },
    )
    # 第一步：若提供了修正参数，直接分叉/更新并重跑
    return time_travel_invoke(
        graph,
        thread_id=thread_id,
        checkpoint_id=checkpoint_id,
        values=values,
        as_node=as_node,
    )


def fork_node_branch(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    *,
    fork_thread_id: str,
    checkpoint_id: str | None = None,
    values: dict[str, Any] | None = None,
    as_node: str | None = None,
) -> RunnableConfig:
    """Hook：基于某个节点的检查点新建独立分支（Fork），以便探索不同分析假设或参数而不污染原线程。

    :param graph: 绑定了持久化 Checkpointer 的已编译工作流图。
    :type graph: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
    :param thread_id: 源工作流线程标识符。
    :type thread_id: str
    :param fork_thread_id: 派生的新分支线程标识符。
    :type fork_thread_id: str
    :param checkpoint_id: 要进行分支派生的源检查点 ID；默认为最新检查点。
    :type checkpoint_id: str | None
    :param values: 可选在新分支中注入的对比实验状态增量字典。
    :type values: dict[str, typing.Any] | None
    :param as_node: 可选的更新来源节点。
    :type as_node: str | None
    :return: 指向新派生分支起点的 RunnableConfig。
    :rtype: langchain_core.runnables.RunnableConfig
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "fork_node_branch_called",
        extra={
            "event_fields": {
                "source_thread_id": thread_id,
                "fork_thread_id": fork_thread_id,
                "checkpoint_id": checkpoint_id,
            }
        },
    )
    # 第一步：调用 time_travel 引擎的跨线程检查点克隆机制
    forked_cfg = fork_checkpoint(
        graph,
        thread_id,
        checkpoint_id,
        values=values,
        as_node=as_node,
        fork_thread_id=fork_thread_id,
    )
    # 第二步：输出分支创建日志并返回新配置
    logger.info(
        "fork_node_branch_completed",
        extra={"event_fields": {"fork_thread_id": fork_thread_id}},
    )
    return forked_cfg


__all__ = [
    "fork_node_branch",
    "inspect_node_checkpoints",
    "modify_node_state",
    "retry_node",
]
