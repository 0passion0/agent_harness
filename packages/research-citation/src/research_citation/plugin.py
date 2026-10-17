"""仅通过包入口点（entry point）加载的独立领域插件实现模块。"""

from importlib.resources import files

from vertical_harness.contracts import (
    BuildServices,
    Exposure,
    PluginContribution,
    PluginManifest,
    SkillAsset,
    ToolContribution,
)

from research_citation.tools import scan_numeric


class ResearchCitationPlugin:
    """学术引用审计插件实现类；不侵入或修改宿主组合逻辑。"""

    # 插件元数据声明清单
    manifest = PluginManifest(
        plugin_id="research.citations",
        api_version=1,
        version="0.1.0",
        description="Deterministic in-text reference checks for research manuscripts",
    )

    def build(self, services: BuildServices) -> PluginContribution:
        """返回当前插件的原生工具与技能文件声明，无需修改宿主核心逻辑。

        :param services: 宿主在构建期注入的服务实例（如工作区路径与存储对象）。
        :return: 包含工具、技能资产与提示指令的声明式 Deep Agents 插件贡献对象。
        """
        # 从包内静态资源中读取 numeric-citation-audit 技能的 SKILL.md 文档内容
        skill_text = (
            files("research_citation")
            .joinpath("skills", "numeric-citation-audit", "SKILL.md")
            .read_text(encoding="utf-8")
        )

        # 组装并返回插件贡献契约对象：注册技能门控工具、技能静态资产与系统提示词引导
        return PluginContribution(
            # 将数字引用校验工具声明为技能门控（Exposure.SKILL），仅在激活技能时可见
            tools=[ToolContribution(tool=scan_numeric, exposure=Exposure.SKILL)],
            # 将 SKILL.md 声明为虚拟技能文件挂载至 /skills 目录下
            skills=[SkillAsset(relative_path="numeric-citation-audit/SKILL.md", content=skill_text)],
            # 注入引导大模型优先使用引用审计技能且仅汇报核实不一致的指令
            instructions="Use numeric-citation-audit skill for bracket-numbered references. Report only verified inconsistencies.",
        )

