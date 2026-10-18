"""Check installed distribution assets and entry points without provider access."""

import argparse
import os
import tempfile
from importlib.metadata import distribution
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from vertical_harness import HarnessSettings, RunContext, open_agent
from vertical_harness.engine import compose
from vertical_harness.plugins import discover_plugins

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--core-only", action="store_true")
core_only = parser.parse_args().core_only
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
print(
    "Installed wheels: core native graph OK"
    if core_only
    else "Installed wheels: entry point, skill, type markers and native graph OK"
)
