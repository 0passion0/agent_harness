"""Standalone plugin loaded only through a package entry point."""

from importlib.resources import files

from research_citation.tools import scan_numeric
from vertical_harness.contracts import (
    BuildServices,
    Exposure,
    PluginContribution,
    PluginManifest,
    SkillAsset,
    ToolContribution,
)


class ResearchCitationPlugin:
    """Plugin does not import or modify harness composition logic."""

    manifest = PluginManifest(
        plugin_id="research.citations",
        api_version=1,
        version="0.1.0",
        description="Deterministic in-text reference checks for research manuscripts",
    )

    def build(self, services: BuildServices) -> PluginContribution:
        """Return native tools and skill files without modifying the host.

        :param services: Build-time host services.
        :return: Declarative Deep Agents contributions.
        """
        skill_text = (
            files("research_citation")
            .joinpath("skills", "numeric-citation-audit", "SKILL.md")
            .read_text(encoding="utf-8")
        )
        return PluginContribution(
            tools=[ToolContribution(tool=scan_numeric, exposure=Exposure.SKILL)],
            skills=[SkillAsset(relative_path="numeric-citation-audit/SKILL.md", content=skill_text)],
            instructions="Use numeric-citation-audit skill for bracket-numbered references. Report only verified inconsistencies.",
        )
