"""检查已安装的轮子分发包资产、类型标记与原生计算图，无需连接大模型提供商网络。"""

from __future__ import annotations

import logging
import os
import tempfile
from importlib.metadata import distribution
from pathlib import Path

from langchain_core.messages import AIMessage
from wheel_model import Model

from vertical_harness.core.compose import AgentComposer
from vertical_harness.core.context import RunContext
from vertical_harness.core.routing import ModelRouting
from vertical_harness.models import HarnessSettings

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

logger = logging.getLogger(__name__)


class WheelCheck:
    @staticmethod
    def model(settings: HarnessSettings) -> Model:
        return Model(responses=[AIMessage(content="wheel-ok")])

    @staticmethod
    def check_wheels() -> None:
        """验证已安装的分发包中包含 py.typed 及有效的原生计算图。"""
        logger.info("checking_wheels_started", extra={"event_fields": {"mode": "core"}})
        package = "vertical-agent-harness"
        files = [str(path).replace("\\", "/") for path in distribution(package).files or ()]
        assert any(path.endswith("vertical_harness/py.typed") for path in files), (
            "missing vertical_harness/py.typed"
        )

        assert not any(
            "vertical_plugins/" in path
            or path.endswith(("vertical_harness/contracts.py", "vertical_harness/workflow.py"))
            for path in files
        )

        ModelRouting.init_configured_model = WheelCheck.model

        with tempfile.TemporaryDirectory() as directory:
            config = HarnessSettings(
                _env_file=None,
                model="test:model",
                workspace_root=Path(directory) / "workspace",
                store_backend="memory",
            )
            with AgentComposer.open_agent(config) as agent:
                output = agent.invoke(
                    {"messages": [("user", "check core")]},
                    config={"configurable": {"thread_id": "wheel-core"}},
                    context=RunContext("t", "u"),
                )
                assert output["messages"][-1].content == "wheel-ok"
        print("Installed wheel: core-only native graph OK")

    @staticmethod
    def main() -> None:
        WheelCheck.check_wheels()


if __name__ == "__main__":
    WheelCheck.main()
