"""领域插件抽象基类与工作流插件模板基类：宿主唯一的插件装配契约。"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any, ClassVar

from vertical_plugins.common.contracts import (
    BuildServices,
    PluginContribution,
    PluginManifest,
    SkillAsset,
    ToolContribution,
)
from vertical_plugins.common.prompts import format_plugin_instruction
from vertical_plugins.common.workflow import (
    BaseWorkflow,
    PluginRuntime,
    WorkflowContext,
    WorkflowHooks,
)


class DomainPlugin(ABC):
    """Python 打包入口点加载的领域插件抽象基类。

    具体插件必须显式继承本类，并实现 ``manifest`` 与 ``build``；缺少任一抽象成员时无法实例化。
    基类刻意不提供默认 ``open`` / ``aopen``：宿主通过这两个可选钩子判断资源型插件，
    默认实现会改变该判断。
    """

    @property
    @abstractmethod
    def manifest(self) -> PluginManifest:
        """返回插件元数据描述清单；具体插件可直接以类属性覆盖。

        :return: 插件清单。
        :rtype: vertical_plugins.common.contracts.PluginManifest
        :author: tongliga
        :date: 2026-10-19
        """

    @abstractmethod
    def build(self, services: BuildServices) -> PluginContribution:
        """创建该插件向 Deep Agents / LangChain 提供的原生贡献。

        :param services: 宿主在构建期注入的服务实例集合。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 包含工作流、工具、技能、中间件、子智能体与系统指令的插件贡献声明对象。
        :rtype: vertical_plugins.common.contracts.PluginContribution
        :author: tongliga
        :date: 2026-10-19
        """


class WorkflowPlugin(DomainPlugin, ABC):
    """带固定工作流的领域插件模板基类。

    统一负责：
    1. 自动发现包内 ``skills/**/SKILL.md`` 技能资产；
    2. 按需构建绑定 ``WorkflowContext`` 的 ``BaseWorkflow`` 与 ``PluginRuntime``；
    3. 组装 ``PluginContribution``（初始化阶段默认只暴露技能说明与受控工作流声明，不直接暴露内部全量工具集）。
    """

    workflow_cls: ClassVar[type[BaseWorkflow] | None] = None
    skill_package: ClassVar[str | None] = None
    instructions_template: ClassVar[str] = ""
    expose_tools_by_default: ClassVar[bool] = False

    def resolve_package_name(self) -> str:
        """解析插件所属 Python 包名以用于资源扫描。

        :return: Python 包名。
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：优先使用显式声明的 skill_package
        if self.skill_package:
            return self.skill_package
        # 第二步：从模块对象的 __package__ 解析所属包名
        module = sys.modules.get(self.__class__.__module__)
        package_name = getattr(module, "__package__", None)
        if package_name:
            return str(package_name)
        return self.__class__.__module__

    def discover_skills(self) -> tuple[SkillAsset, ...]:
        """自动扫描并加载插件包内 ``skills/**/SKILL.md`` 资产。

        :return: 按相对路径排序的技能资产元组。
        :rtype: tuple[vertical_plugins.common.contracts.SkillAsset, ...]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：定位包内 skills 目录
        package_name = self.resolve_package_name()
        try:
            package_root = files(package_name)
        except (ModuleNotFoundError, TypeError):
            return ()
        skills_root = package_root.joinpath("skills")
        if not skills_root.is_dir():
            return ()

        # 第二步：递归收集所有 SKILL.md 文件
        discovered: list[SkillAsset] = []
        self._collect_skill_files(skills_root, prefix="", output=discovered)
        discovered.sort(key=lambda item: item.relative_path)
        return tuple(discovered)

    def _collect_skill_files(
        self,
        current_dir: Traversable,
        *,
        prefix: str,
        output: list[SkillAsset],
    ) -> None:
        """递归遍历目录收集 ``SKILL.md``。

        :param current_dir: 当前遍历目录。
        :type current_dir: importlib.resources.abc.Traversable
        :param prefix: 相对 ``skills/`` 的路径前缀。
        :type prefix: str
        :param output: 技能资产收集列表。
        :type output: list[vertical_plugins.common.contracts.SkillAsset]
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：对当前目录子项按名称排序遍历
        for entry in sorted(current_dir.iterdir(), key=lambda item: item.name):
            if entry.is_dir():
                next_prefix = f"{prefix}/{entry.name}" if prefix else entry.name
                self._collect_skill_files(entry, prefix=next_prefix, output=output)
            elif entry.is_file() and entry.name == "SKILL.md" and prefix:
                relative_path = f"{prefix}/SKILL.md"
                output.append(
                    SkillAsset(
                        relative_path=relative_path,
                        content=entry.read_text(encoding="utf-8"),
                    )
                )

    def create_context(self, services: BuildServices | None = None, **overrides: Any) -> WorkflowContext:
        """根据宿主构建服务与覆盖参数创建工作流运行上下文。

        :param services: 宿主构建服务，可选。
        :type services: vertical_plugins.common.contracts.BuildServices | None
        :param overrides: 显式覆盖的上下文字段。
        :type overrides: Any
        :return: 工作流运行上下文。
        :rtype: vertical_plugins.common.workflow.WorkflowContext
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：从 BuildServices 派生基础上下文，或构建默认上下文
        base_ctx = WorkflowContext.from_services(services, plugin_id=self.manifest.plugin_id)
        # 第二步：合并调用方覆盖字段
        if not overrides:
            return base_ctx
        return base_ctx.with_overrides(**overrides)

    def create_workflow(
        self,
        services: BuildServices | None = None,
        *,
        hooks: WorkflowHooks | None = None,
        **context_overrides: Any,
    ) -> BaseWorkflow:
        """实例化插件绑定的工作流对象。

        :param services: 宿主构建服务，可选。
        :type services: vertical_plugins.common.contracts.BuildServices | None
        :param hooks: 可选生命周期钩子。
        :type hooks: vertical_plugins.common.workflow.WorkflowHooks | None
        :param context_overrides: 显式覆盖的上下文字段。
        :type context_overrides: Any
        :return: 工作流实例。
        :rtype: vertical_plugins.common.workflow.BaseWorkflow
        :raises TypeError: 未声明 ``workflow_cls`` 时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：校验插件是否声明了 workflow_cls（兼容 workflow_class 别名）
        wf_cls = self.workflow_cls or getattr(self, "workflow_class", None)
        if wf_cls is None:
            raise TypeError(f"Plugin '{self.__class__.__name__}' must define class attribute 'workflow_cls'.")
        # 第二步：创建上下文并实例化工作流
        context = self.create_context(services, **context_overrides)
        return wf_cls(context=context, hooks=hooks)

    def create_runtime(
        self,
        services: BuildServices | None = None,
        *,
        hooks: WorkflowHooks | None = None,
        **context_overrides: Any,
    ) -> PluginRuntime:
        """创建绑定当前插件工作流的 ``PluginRuntime`` 运行与观测门面。

        :param services: 宿主构建服务，可选。
        :type services: vertical_plugins.common.contracts.BuildServices | None
        :param hooks: 可选生命周期钩子。
        :type hooks: vertical_plugins.common.workflow.WorkflowHooks | None
        :param context_overrides: 显式覆盖的上下文字段。
        :type context_overrides: Any
        :return: 插件运行实例。
        :rtype: vertical_plugins.common.workflow.PluginRuntime
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：构建工作流并包装为 PluginRuntime
        workflow = self.create_workflow(services, hooks=hooks, **context_overrides)
        return workflow.create_runtime()

    def build_tools(self, services: BuildServices) -> tuple[ToolContribution, ...]:
        """子类可选覆写的内部工具构建钩子（默认不向主 Agent 暴露）。

        :param services: 宿主构建服务。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 工具贡献元组。
        :rtype: tuple[vertical_plugins.common.contracts.ToolContribution, ...]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：默认返回空元组，防止未授权工具集直接暴露给主 Agent
        _ = services
        return ()

    def build_instructions(self, services: BuildServices) -> str:
        """构建注入主 Agent 的轻量插件系统提示。

        :param services: 宿主构建服务。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 格式化后的系统提示文本。
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：若声明了指令模板则按工作区路径格式化
        if not self.instructions_template:
            return ""
        return format_plugin_instruction(self.instructions_template, str(services.workspace_root)) or ""

    def build(self, services: BuildServices) -> PluginContribution:
        """统一组装工作流插件的技能、工作流与系统指令贡献。

        :param services: 宿主在构建期注入的服务实例集合。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 插件贡献对象。
        :rtype: vertical_plugins.common.contracts.PluginContribution
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：构建受控工作流贡献与自动发现的技能资产
        workflow = self.create_workflow(services)
        skills = self.discover_skills()
        tools = self.build_tools(services) if self.expose_tools_by_default else ()
        instructions = self.build_instructions(services)

        # 第二步：组装并返回 PluginContribution
        return PluginContribution(
            workflows=[workflow.to_contribution()],
            tools=list(tools),
            skills=list(skills),
            instructions=instructions,
        )


__all__ = ["DomainPlugin", "WorkflowPlugin"]
