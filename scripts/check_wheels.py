"""检查已安装的轮子分发包资产、类型标记与 entry points 注册，无需连接大模型提供商网络。"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from vertical_harness import HarnessSettings, RunContext, open_agent
from vertical_harness.engine import compose
from vertical_harness.engine.plugin_loader import discover_plugins

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

logger = logging.getLogger(__name__)


class Model(FakeMessagesListChatModel):
    """用于 Wheel 离线验证的虚假聊天模型。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        """绑定工具，直接返回自身。"""
        return self


def check_wheels(*, mode: str = "base") -> None:
    """验证已安装的分发包中包含 py.typed、SKILL.md、guides 及有效的 entry point 和原生计算图。

    :param mode: 校验模式：'core-only'（仅核心宿主）、'base'（基础安装，加载引用插件）、'with-analysis'（包含 analysis extra）。
    :raises AssertionError: 当关键资产文件缺失或智能体响应不符合预期时抛出。
    """
    logger.info("checking_wheels_started", extra={"event_fields": {"mode": mode}})
    package = "vertical-agent-harness"
    files = [str(path).replace("\\", "/") for path in distribution(package).files or ()]

    # 核对类型标记与技能、指南资产在 wheel 中完整打包
    assert any(path.endswith("vertical_harness/py.typed") for path in files), "missing vertical_harness/py.typed"
    assert any(path.endswith("vertical_plugins/py.typed") for path in files), "missing vertical_plugins/py.typed"
    assert any(path.endswith("numeric-citation-audit/SKILL.md") for path in files), "missing citation SKILL.md"
    assert any(path.endswith("scientific-data-analysis/SKILL.md") for path in files), "missing data analysis SKILL.md"
    assert any(path.endswith("guides/tabular-data.md") for path in files), "missing tabular-data guide"

    compose.init_configured_model = lambda *_: Model(responses=[AIMessage(content="wheel-ok")])

    if mode == "core-only":
        with tempfile.TemporaryDirectory() as directory:
            config = HarnessSettings(
                _env_file=None,
                model="test:model",
                workspace_root=Path(directory) / "workspace",
                store_backend="memory",
                enable_memory=False,
                enabled_plugins=[],
            )
            with open_agent(config) as agent:
                output = agent.invoke(
                    {"messages": [("user", "check core")]},
                    config={"configurable": {"thread_id": "wheel-core"}},
                    context=RunContext("t", "u"),
                )
                assert output["messages"][-1].content == "wheel-ok"
        print("Installed wheel: core-only native graph OK")
        return

    if mode == "base":
        # 基础安装：加载引用插件
        (plugin,) = discover_plugins(["research.citations"])
        assert plugin.manifest.plugin_id == "research.citations"

        with tempfile.TemporaryDirectory() as directory:
            config = HarnessSettings(
                _env_file=None,
                model="test:model",
                workspace_root=Path(directory) / "workspace",
                store_backend="memory",
                enable_memory=False,
                enabled_plugins=["research.citations"],
            )
            with open_agent(config) as agent:
                output = agent.invoke(
                    {"messages": [("user", "check installed citation plugin")]},
                    config={"configurable": {"thread_id": "wheel-citation"}},
                    context=RunContext("t", "u"),
                )
                assert output["messages"][-1].content == "wheel-ok"

        # 基础安装若未安装 duckdb/openpyxl，尝试启用 data_analysis 必须直接抛出原始 ModuleNotFoundError
        try:
            import duckdb  # noqa: F401
            has_duckdb = True
        except ModuleNotFoundError:
            has_duckdb = False

        if not has_duckdb:
            try:
                discover_plugins(["data_analysis"])
                raise AssertionError("Expected ModuleNotFoundError for missing duckdb when loading data_analysis in base wheel")
            except ModuleNotFoundError as exc:
                assert "duckdb" in str(exc)

        print("Installed wheel: base install (research.citations entry point, skills, type markers, native graph) OK")
        return

    if mode == "with-analysis":
        # 安装了 analysis extra：同时验证 data_analysis 与 research.citations
        plugins = discover_plugins(["research.citations", "data_analysis"])
        assert len(plugins) == 2
        p_map = {p.manifest.plugin_id: p for p in plugins}
        assert "research.citations" in p_map
        assert "data_analysis" in p_map

        # 验证指南与技能资源完整可读
        from vertical_plugins.data_analysis import read_analysis_guide
        guide_text = read_analysis_guide("tabular-data")
        assert len(guide_text) > 50

        with tempfile.TemporaryDirectory() as directory:
            config = HarnessSettings(
                _env_file=None,
                model="test:model",
                workspace_root=Path(directory) / "workspace",
                store_backend="memory",
                enable_memory=False,
                enabled_plugins=["research.citations", "data_analysis"],
            )
            with open_agent(config) as agent:
                output = agent.invoke(
                    {"messages": [("user", "check installed plugins with analysis")]},
                    config={"configurable": {"thread_id": "wheel-analysis"}},
                    context=RunContext("t", "u"),
                )
                assert output["messages"][-1].content == "wheel-ok"

        print("Installed wheel with analysis extra: entry points, skills, guides, type markers and native graphs OK")


def main() -> None:
    """脚本命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["core-only", "base", "with-analysis"],
        default="base",
        help="Validation mode: core-only, base (default), or with-analysis",
    )
    parser.add_argument("--core-only", action="store_true", help="Alias for --mode core-only")
    parser.add_argument("--with-analysis", action="store_true", help="Alias for --mode with-analysis")
    parser.add_argument("--base", action="store_true", help="Alias for --mode base")
    args = parser.parse_args()

    mode = args.mode
    if args.core_only:
        mode = "core-only"
    elif args.with_analysis:
        mode = "with-analysis"
    elif args.base:
        mode = "base"

    check_wheels(mode=mode)


if __name__ == "__main__":
    main()
