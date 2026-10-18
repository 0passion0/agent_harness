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
from vertical_harness.plugins import discover_plugins

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

logger = logging.getLogger(__name__)


class Model(FakeMessagesListChatModel):
    """用于 Wheel 离线验证的虚假聊天模型。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        """绑定工具，直接返回自身。"""
        return self


def check_wheels(*, core_only: bool = False) -> None:
    """验证已安装的分发包中包含 py.typed、SKILL.md 及有效的 entry point 和原生计算图。

    :param core_only: 若为 True 则仅校验核心 harness 包，不校验领域插件。
    :raises AssertionError: 当关键资产文件缺失或智能体响应不符合预期时抛出。
    """
    logger.info("checking_wheels_started", extra={"event_fields": {"core_only": core_only}})
    packages = (
        ("vertical-agent-harness",)
        if core_only
        else ("vertical-agent-harness", "vertical-plugin-research-citation")
    )
    for package in packages:
        files = [str(path).replace("\\", "/") for path in distribution(package).files or ()]
        assert any(path.endswith("/py.typed") for path in files), package
        if package.endswith("citation"):
            assert any(path.endswith("/SKILL.md") for path in files)

    if not core_only:
        (plugin,) = discover_plugins(["research.citations"])
        assert plugin.manifest.plugin_id == "research.citations"

    compose.init_configured_model = lambda *_: Model(responses=[AIMessage(content="wheel-ok")])
    with tempfile.TemporaryDirectory() as directory:
        config = HarnessSettings(
            _env_file=None,
            model="test:model",
            workspace_root=Path(directory) / "workspace",
            store_backend="memory",
            enable_memory=False,
            enabled_plugins=[] if core_only else ["research.citations"],
        )
        with open_agent(config) as agent:
            output = agent.invoke(
                {"messages": [("user", "check installed plugin")]},
                config={"configurable": {"thread_id": "wheel"}},
                context=RunContext("t", "u"),
            )
            assert output["messages"][-1].content == "wheel-ok"
    logger.info("checking_wheels_passed")
    print(
        "Installed wheels: core native graph OK"
        if core_only
        else "Installed wheels: entry point, skill, type markers and native graph OK"
    )


def main() -> None:
    """脚本命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-only", action="store_true", help="Only check core harness package")
    args = parser.parse_args()
    check_wheels(core_only=args.core_only)


if __name__ == "__main__":
    main()
