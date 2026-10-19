"""领域插件公共层导出：抽象基类、清单、贡献、服务及运行上下文契约。

公共层不导入任何业务插件，亦不依赖宿主 `vertical_harness`。
"""

from vertical_plugins.common.base import DomainPlugin
from vertical_plugins.common.contracts import (
    BuildServices,
    Exposure,
    PluginContribution,
    PluginIdStr,
    PluginManifest,
    RunContext,
    SemVerStr,
    SideEffect,
    SkillAsset,
    StrictModel,
    SubAgentContextMode,
    SubAgentSpec,
    ToolContribution,
    WorkflowContribution,
    WorkflowInputState,
    WorkflowInternalState,
    WorkflowMode,
    WorkflowNameStr,
    WorkflowOutputState,
)
from vertical_plugins.common.prompts import format_plugin_instruction

__all__ = [
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
    "SubAgentContextMode",
    "SubAgentSpec",
    "ToolContribution",
    "WorkflowContribution",
    "WorkflowInputState",
    "WorkflowInternalState",
    "WorkflowMode",
    "WorkflowNameStr",
    "WorkflowOutputState",
    "format_plugin_instruction",
]
