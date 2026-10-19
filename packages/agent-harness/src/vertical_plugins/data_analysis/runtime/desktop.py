"""数据分析插件在桌面客户端应用中的状态快照与产物映射模块。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 桌面客户端识别该插件的元数据描述符
DESCRIPTOR: dict[str, Any] = {
    "id": "data_analysis",
    "name": "科研数据分析",
    "description": "特定科研任务下提供研究问答、分析证据和报告交付。",
    "interaction_kinds": ["research_question"],
}


def _resolve_workspace_path(workspace: Path | Any) -> Path:
    """提取工作区物理路径。"""
    if isinstance(workspace, Path):
        return workspace.resolve()
    if hasattr(workspace, "workdir"):
        return Path(workspace.workdir).resolve()
    if hasattr(workspace, "root_dir"):
        return Path(workspace.root_dir).resolve()
    if hasattr(workspace, "cwd"):
        return Path(workspace.cwd).resolve()
    return Path(str(workspace)).resolve()


def snapshot(state_values: dict[str, Any], workspace: Path | Any) -> dict[str, Any]:
    """将科研分析状态转换为桌面客户端可读取的投影快照。

    :param state_values: 当前 LangGraph 状态字典。
    :type state_values: dict[str, typing.Any]
    :param workspace: 当前工作区根目录路径或存储后端。
    :type workspace: pathlib.Path | typing.Any
    :return: 桌面端可用的插件状态快照字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("desktop_snapshot_started")
    workdir = _resolve_workspace_path(workspace)
    # 第一步：提取科研插件相关的核心状态键值
    result: dict[str, Any] = {
        key: state_values[key]
        for key in ("active_plugin", "analysis_executions", "analysis_report_path")
        if key in state_values
    }

    # 第二步：如果存在分析报告，计算其相对工作区的虚拟路径并挂载到快照中
    if result.get("analysis_report_path"):
        report = Path(result["analysis_report_path"]).resolve()
        if report.is_relative_to(workdir) and report.is_file():
            virtual = "/workspace/" + report.relative_to(workdir).as_posix()
            result["report_open_path"] = virtual
            logger.debug("report_open_path_computed", extra={"event_fields": {"path": virtual}})

    return result


def artifact_paths(state_values: dict[str, Any], workspace: Path | Any) -> list[str]:
    """列出科研分析插件本轮发布的报告与数据产物虚拟路径。

    :param state_values: 当前图状态字典。
    :type state_values: dict[str, typing.Any]
    :param workspace: 当前工作区存储后端或路径。
    :type workspace: pathlib.Path | typing.Any
    :return: 发布的产物虚拟路径列表。
    :rtype: list[str]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：判断当前激活插件是否为 data_analysis，若不是则直接返回空
    if state_values.get("active_plugin") != "data_analysis":
        return []

    # 第二步：获取快照中的报告打开路径并包装为产物列表返回
    report = snapshot(state_values, workspace).get("report_open_path")
    return [report] if report else []


__all__ = [
    "DESCRIPTOR",
    "artifact_paths",
    "snapshot",
]
