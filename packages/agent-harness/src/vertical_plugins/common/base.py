"""领域插件抽象基类：宿主唯一的插件装配契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from vertical_plugins.common.contracts import BuildServices, PluginContribution, PluginManifest


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


__all__ = ["DomainPlugin"]
