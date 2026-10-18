"""基于 Python 打包入口点（entry points）的可信插件发现与契约校验模块。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from importlib.metadata import EntryPoint, entry_points

from vertical_harness.plugins.contracts import DomainPlugin, PluginManifest

# 定义插件在 Python entry points 中注册的分组名称
PLUGIN_GROUP = "vertical_agent.plugins"


def _index_matching_entry_points(allowlist_set: set[str]) -> dict[str, EntryPoint]:
    """扫描 entry points 分组并索引白名单内的候选入口点。

    :param allowlist_set: 允许加载的插件标识符集合。
    :return: 插件标识符到对应 EntryPoint 实例的映射字典。
    :raises ValueError: 当发现同名重复注册的入口点时抛出。
    """
    matches: dict[str, EntryPoint] = {}
    for ep in entry_points(group=PLUGIN_GROUP):
        if ep.name not in allowlist_set:
            continue
        if ep.name in matches:
            raise ValueError(f"Duplicate entry point: {ep.name}")
        matches[ep.name] = ep
    return matches


def _instantiate_and_verify(identifier: str, ep: EntryPoint) -> DomainPlugin:
    """加载入口点工厂，实例化插件并严格校验其元数据清单与协议实现。

    :param identifier: 期望加载的插件 ID。
    :param ep: 对应的 Python 打包 EntryPoint 对象。
    :return: 校验通过的 DomainPlugin 实例。
    :raises ValueError: 当插件清单 ID 与入口点名称不一致时抛出。
    :raises TypeError: 当插件未实现可调用的 build() 方法时抛出。
    """
    factory = ep.load()
    plugin = factory()

    manifest = PluginManifest.model_validate(plugin.manifest)
    if manifest.plugin_id != identifier:
        raise ValueError(f"Entry point / manifest mismatch: {identifier}")

    if not callable(getattr(plugin, "build", None)):
        raise TypeError(f"Plugin {identifier} does not implement build()")

    return plugin


def discover_plugins(allowlist: Sequence[str]) -> list[DomainPlugin]:
    """加载宿主显式启用的已安装插件。

    :param allowlist: 当前部署允许加载的插件标识符白名单序列。
    :return: 按照配置顺序排列的已初始化插件实例列表。
    :raises ValueError: 当白名单包含重复 ID、发现重复入口点、存在未安装插件或入口点与清单 ID 不匹配时抛出。
    :raises TypeError: 当加载的插件对象未实现可调用的 build() 方法时抛出。
    """
    # 使用 Counter 检查白名单中是否存在重复的插件 ID，防止重复注册
    duplicates = [pid for pid, count in Counter(allowlist).items() if count > 1]
    if duplicates:
        raise ValueError(f"Duplicate plugin IDs in allowlist: {duplicates}")

    allowlist_set = set(allowlist)
    matches = _index_matching_entry_points(allowlist_set)

    # 校验白名单中是否存在未安装或未注册的插件
    missing = sorted(allowlist_set - matches.keys())
    if missing:
        raise ValueError(f"Required plugins not installed: {missing}")

    # 严格按照白名单中指定的顺序加载并实例化插件
    return [_instantiate_and_verify(identifier, matches[identifier]) for identifier in allowlist]


__all__ = [
    "PLUGIN_GROUP",
    "discover_plugins",
]
