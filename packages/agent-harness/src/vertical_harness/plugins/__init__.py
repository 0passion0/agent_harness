"""领域插件契约与入口点发现子包公共导出。"""

from vertical_harness.plugins.contracts import (
    BuildServices,
    DomainPlugin,
    Exposure,
    PluginContribution,
    PluginIdStr,
    PluginManifest,
    RunContext,
    SemVerStr,
    SideEffect,
    SkillAsset,
    StrictModel,
    SubAgentSpec,
    ToolContribution,
)
from vertical_harness.plugins.discovery import PLUGIN_GROUP, discover_plugins

__all__ = [
    "PLUGIN_GROUP",
    "BuildServices",
    "DomainPlugin",
    "Exposure",
    "PluginContribution",
    "PluginIdStr",
    "PluginManifest",
    "RunContext",
    "SemVerStr",
    "SideEffect",
    "SkillAsset",
    "StrictModel",
    "SubAgentSpec",
    "ToolContribution",
    "discover_plugins",
]
