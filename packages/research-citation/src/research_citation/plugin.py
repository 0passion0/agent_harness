"""仅通过包入口点（entry point）加载的独立领域插件实现模块。"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.resources import files

from research_citation.checker.tools import ResearchCitationToolkit
from vertical_harness.plugins.contracts import (
    BuildServices,
    Exposure,
    PluginContribution,
    PluginManifest,
    SkillAsset,
    ToolContribution,
)

SKILL_RELATIVE_PATH = "numeric-citation-audit/SKILL.md"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_skill_asset() -> SkillAsset:
    """从包内静态资源读取并缓存 numeric-citation-audit 技能的 SKILL.md 资产。

    :return: 经路径校验的 SkillAsset 实例。
    """
    logger.debug("skill_resource_loading")
    skill_text = (
        files("research_citation")
        .joinpath("skills", "numeric-citation-audit", "SKILL.md")
        .read_text(encoding="utf-8")
    )
    asset = SkillAsset(relative_path=SKILL_RELATIVE_PATH, content=skill_text)
    logger.debug(
        "skill_resource_loaded", extra={"event_fields": {"skill_path": SKILL_RELATIVE_PATH}}
    )
    return asset


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
        toolkit = ResearchCitationToolkit()
        logger.debug("citation_plugin_build_started")
        return PluginContribution(
            tools=ToolContribution.from_toolkit(toolkit, exposure=Exposure.SKILL),
            skills=[_load_skill_asset()],
            instructions=(
                "Use numeric-citation-audit skill for bracket-numbered references. "
                "Report only verified inconsistencies."
            ),
        )


__all__ = [
    "SKILL_RELATIVE_PATH",
    "ResearchCitationPlugin",
]
