"""检查已安装的轮子分发包资产、类型标记与原生计算图，无需连接大模型提供商网络。"""

from __future__ import annotations

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

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

logger = logging.getLogger(__name__)


class Model(FakeMessagesListChatModel):
    """用于 Wheel 离线验证的虚假聊天模型。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self


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

    compose.init_configured_model = lambda *_: Model(responses=[AIMessage(content="wheel-ok")])

    with tempfile.TemporaryDirectory() as directory:
        config = HarnessSettings(
            _env_file=None,
            model="test:model",
            workspace_root=Path(directory) / "workspace",
            store_backend="memory",
        )
        with open_agent(config) as agent:
            output = agent.invoke(
                {"messages": [("user", "check core")]},
                config={"configurable": {"thread_id": "wheel-core"}},
                context=RunContext("t", "u"),
            )
            assert output["messages"][-1].content == "wheel-ok"
    print("Installed wheel: core-only native graph OK")


def main() -> None:
    check_wheels()


if __name__ == "__main__":
    main()
