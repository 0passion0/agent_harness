"""基于 Python 打包入口点（entry points）的可信插件发现与契约校验模块。"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

from langchain_core.runnables import Runnable

from vertical_harness.plugins.contracts import BuildServices, DomainPlugin, PluginManifest

# 定义插件在 Python entry points 中注册的分组名称
PLUGIN_GROUP = "vertical_agent.plugins"
logger = logging.getLogger(__name__)


def _index_matching_entry_points(allowlist_set: set[str]) -> dict[str, EntryPoint]:
    """扫描 entry points 分组并索引白名单内的候选入口点。

    :param allowlist_set: 允许加载的插件标识符集合。
    :type allowlist_set: set[str]
    :return: 插件标识符到对应 EntryPoint 实例的映射字典。
    :rtype: dict[str, importlib.metadata.EntryPoint]
    :raises ValueError: 当发现同名重复注册的入口点时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：初始化匹配结果字典
    matches: dict[str, EntryPoint] = {}
    # 第二步：遍历指定插件分组下所有注册的 entry points
    for ep in entry_points(group=PLUGIN_GROUP):
        if ep.name not in allowlist_set:
            continue
        if ep.name in matches:
            logger.error(
                "plugin_entry_point_duplicate", extra={"event_fields": {"plugin_id": ep.name}}
            )
            raise ValueError(f"Duplicate entry point: {ep.name}")
        matches[ep.name] = ep
    # 第三步：记录索引完成调试日志并返回
    logger.debug(
        "plugin_entry_points_indexed", extra={"event_fields": {"plugin_count": len(matches)}}
    )
    return matches


def _instantiate_and_verify(identifier: str, ep: EntryPoint) -> DomainPlugin:
    """加载入口点工厂，实例化插件并严格校验其元数据清单与协议实现。

    :param identifier: 期望加载的插件 ID。
    :type identifier: str
    :param ep: 对应的 Python 打包 EntryPoint 对象。
    :type ep: importlib.metadata.EntryPoint
    :return: 校验通过的 DomainPlugin 实例。
    :rtype: vertical_harness.plugins.contracts.DomainPlugin
    :raises ValueError: 当插件清单 ID 与入口点名称不一致时抛出。
    :raises TypeError: 当插件未实现可调用的 build() 方法时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：加载入口点类或工厂函数并执行无参实例化
    factory = ep.load()
    logger.debug("plugin_factory_loaded", extra={"event_fields": {"plugin_id": identifier}})
    plugin = factory()

    # 第二步：断言 manifest 为合法 PluginManifest 且 plugin_id 与入口点名称完全一致
    if not isinstance(plugin.manifest, PluginManifest):
        raise TypeError(f"Plugin {identifier} manifest must be a PluginManifest instance")
    manifest = PluginManifest.model_validate(plugin.manifest)
    if manifest.plugin_id != identifier:
        raise ValueError(f"Entry point / manifest mismatch: {identifier}")

    # 第三步：断言插件必须实现可调用的 build() 契约方法
    if not callable(getattr(plugin, "build", None)):
        raise TypeError(f"Plugin {identifier} does not implement build()")

    logger.debug("plugin_contract_verified", extra={"event_fields": {"plugin_id": identifier}})
    return plugin


def discover_plugins(allowlist: Sequence[str]) -> list[DomainPlugin]:
    """加载宿主显式启用的已安装插件。

    :param allowlist: 当前部署允许加载的插件标识符白名单序列。
    :type allowlist: collections.abc.Sequence[str]
    :return: 按照配置顺序排列的已初始化插件实例列表。
    :rtype: list[vertical_harness.plugins.contracts.DomainPlugin]
    :raises ValueError: 当白名单包含重复 ID、发现重复入口点、存在未安装插件或入口点与清单 ID 不匹配时抛出。
    :raises TypeError: 当加载的插件对象未实现可调用的 build() 方法时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug(
        "plugin_discovery_started", extra={"event_fields": {"plugin_count": len(allowlist)}}
    )
    # 第一步：使用 Counter 检查白名单中是否存在重复声明的插件 ID
    duplicates = [pid for pid, count in Counter(allowlist).items() if count > 1]
    if duplicates:
        logger.error(
            "plugin_allowlist_duplicates", extra={"event_fields": {"plugin_count": len(duplicates)}}
        )
        raise ValueError(f"Duplicate plugin IDs in allowlist: {duplicates}")

    # 第二步：检索所有已安装的符合白名单的 entry points 并核对未安装插件
    allowlist_set = set(allowlist)
    matches = _index_matching_entry_points(allowlist_set)
    missing = sorted(allowlist_set - matches.keys())
    if missing:
        logger.error("plugins_missing", extra={"event_fields": {"plugin_count": len(missing)}})
        raise ValueError(f"Required plugins not installed: {missing}")

    # 第三步：严格按照白名单中指定的顺序加载并实例化插件列表
    plugins = []
    for identifier in allowlist:
        logger.debug("plugin_loading", extra={"event_fields": {"plugin_id": identifier}})
        try:
            plugin = _instantiate_and_verify(identifier, matches[identifier])
        except Exception as exc:
            logger.error(
                "plugin_load_failed",
                extra={"event_fields": {"plugin_id": identifier, "error_type": type(exc).__name__}},
            )
            raise
        plugins.append(plugin)
        logger.info("plugin_loaded", extra={"event_fields": {"plugin_id": identifier}})
    logger.info(
        "plugin_discovery_completed", extra={"event_fields": {"plugin_count": len(plugins)}}
    )
    return plugins


def discover_workflow_graphs(
    allowlist: Sequence[str],
    services: BuildServices | None = None,
) -> dict[str, Runnable[Any, Any]]:
    """从已启用插件中收集所有预编译 LangGraph 工作流图，组成 Graph Registry。

    适用于构建本地 `CompiledSubAgent` 图表或导出给 Agent Protocol / `langgraph.json` 部署。

    :param allowlist: 允许加载的插件标识符白名单序列。
    :type allowlist: collections.abc.Sequence[str]
    :param services: 可选的构建服务实例；未提供时使用当前工作目录作为默认工作区。
    :type services: vertical_harness.plugins.contracts.BuildServices | None
    :return: `graph_id`（未设置时回退为 `workflow.name`）到可运行图对象的映射字典。
    :rtype: dict[str, langchain_core.runnables.Runnable[typing.Any, typing.Any]]
    :raises ValueError: 当不同工作流注册了重复的图标识符时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：初始化构建服务与目标图注册表映射
    active_services = services or BuildServices(workspace_root=Path("./.agent-workspace").resolve())
    registry: dict[str, Runnable[Any, Any]] = {}

    # 第二步：加载所有启用的插件并调用 build() 检索工作流贡献
    for plugin in discover_plugins(allowlist):
        contribution = plugin.build(active_services)
        for workflow in contribution.workflows:
            if workflow.runnable is None:
                continue
            graph_key = workflow.graph_id or workflow.name
            # 第三步：校验图标识符全局唯一性并注册
            if graph_key in registry:
                raise ValueError(f"Duplicate workflow graph identifier: {graph_key}")
            registry[graph_key] = workflow.runnable
    logger.debug(
        "workflow_graph_registry_built",
        extra={"event_fields": {"workflow_count": len(registry)}},
    )
    return registry


__all__ = [
    "PLUGIN_GROUP",
    "discover_plugins",
    "discover_workflow_graphs",
]
