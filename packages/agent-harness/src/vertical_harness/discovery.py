"""基于 Python 打包入口点（entry points）的可信插件发现模块。"""

from importlib.metadata import entry_points

from vertical_harness.contracts import DomainPlugin, PluginManifest

# 定义插件在 Python entry points 中注册的分组名称
PLUGIN_GROUP = "vertical_agent.plugins"


def discover_plugins(allowlist: list[str]) -> list[DomainPlugin]:
    """加载宿主显式启用的已安装插件。

    :param allowlist: 当前部署允许加载的插件标识符白名单列表。
    :return: 按照配置顺序排列的已初始化插件实例列表。
    :raises ValueError: 当白名单包含重复 ID、发现重复入口点、存在未安装插件或入口点与清单 ID 不匹配时抛出。
    :raises TypeError: 当加载的插件对象未实现可调用的 build() 方法时抛出。
    """
    # 检查白名单中是否存在重复的插件 ID，防止重复注册
    if len(allowlist) != len(set(allowlist)):
        raise ValueError("Duplicate plugin IDs in allowlist")

    # 根据约定的插件分组获取所有通过 entry points 注册的插件候选者
    candidates = entry_points(group=PLUGIN_GROUP)
    matches = {}

    # 遍历候选入口点，筛选出在允许白名单内的插件
    for ep in candidates:
        if ep.name in allowlist:
            # 校验同名入口点，避免插件定义冲突
            if ep.name in matches:
                raise ValueError(f"Duplicate entry point: {ep.name}")
            matches[ep.name] = ep

    # 校验白名单中是否存在未安装或未注册的插件
    missing = set(allowlist) - set(matches)
    if missing:
        raise ValueError(f"Required plugins not installed: {sorted(missing)}")

    # 严格按照白名单中指定的顺序加载并实例化插件
    loaded = []
    for identifier in allowlist:
        # 动态加载入口点对应的工厂函数并调用实例化
        factory = matches[identifier].load()
        plugin = factory()

        # 校验插件元数据清单的合法性，确保其与注册的 ID 一致
        manifest = PluginManifest.model_validate(plugin.manifest)
        if manifest.plugin_id != identifier:
            raise ValueError(f"Entry point / manifest mismatch: {identifier}")

        # 确保插件对象实现了契约所要求的 build 构建方法
        if not callable(getattr(plugin, "build", None)):
            raise TypeError(f"Plugin {identifier} does not implement build()")

        loaded.append(plugin)

    return loaded

