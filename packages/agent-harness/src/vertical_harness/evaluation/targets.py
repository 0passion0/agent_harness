"""评测目标适配层；不修改生产存储、提示词或插件注册。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from langgraph.store.memory import InMemoryStore

from vertical_harness.config import HarnessSettings
from vertical_harness.engine.compose import BASE_PROMPT, aopen_agent
from vertical_harness.evaluation.scoring import input_payload, normalize_outputs
from vertical_harness.plugins import BuildServices, RunContext, discover_plugins


@dataclass(frozen=True)
class EvaluationTarget:
    """main / plugin:<id> / workflow:<name>。插件模式包含该插件的监督者调度。"""

    name: str = "main"

    def __post_init__(self) -> None:
        if self.name != "main" and not (
            self.name.startswith(("plugin:", "workflow:")) and self.name.partition(":")[2]
        ):
            raise ValueError("Target must be main, plugin:<id>, or workflow:<name>")

    @property
    def prompt_key(self) -> str:
        if self.name.startswith("workflow:"):
            raise ValueError(
                "Deterministic workflows have no prompt to optimize; evaluate them directly"
            )
        return self.name.partition(":")[2] if self.name.startswith("plugin:") else "main"

    def isolated_settings(self, settings: HarnessSettings, root: Path) -> HarnessSettings:
        plugins = settings.enabled_plugins
        if self.name.startswith("plugin:"):
            plugin_id = self.prompt_key
            if plugin_id not in plugins:
                raise ValueError(f"Plugin is not enabled: {plugin_id}")
            plugins = [plugin_id]
        return settings.model_copy(
            update={
                "workspace_root": root,
                "store_backend": "memory",
                "store_db_path": ":memory:",
                "checkpoint_db_path": ":memory:",
                "enable_memory": False,
                "enable_user_preferences": False,
                "enable_memory_consolidation": False,
                "enabled_plugins": plugins,
            }
        )

    def baseline_prompt(self, settings: HarnessSettings) -> str:
        if self.prompt_key == "main":
            return BASE_PROMPT
        with TemporaryDirectory(prefix="va-prompt-") as root:
            isolated = self.isolated_settings(settings, Path(root))
            (plugin,) = discover_plugins(isolated.enabled_plugins)
            prompt = plugin.build(BuildServices(Path(root), InMemoryStore())).instructions
        if not prompt.strip():
            raise ValueError("Plugin has no instruction prompt to optimize")
        return prompt


def make_target(settings: HarnessSettings, target: EvaluationTarget, *, prompt: str | None = None):
    async def predict(inputs: dict[str, Any]) -> dict[str, Any]:
        with TemporaryDirectory(prefix="va-eval-") as directory:
            root = Path(directory)
            isolated = target.isolated_settings(settings, root)
            store = InMemoryStore()
            context = RunContext(tenant_id="evaluation", user_id=str(uuid4()))
            payload = input_payload(inputs)
            config = {
                "configurable": {"thread_id": str(uuid4())},
                "metadata": {"evaluation_target": target.name},
            }
            if target.name.startswith("workflow:"):
                name = target.name.partition(":")[2]
                workflows = [
                    workflow
                    for plugin in discover_plugins(isolated.enabled_plugins)
                    for workflow in plugin.build(BuildServices(root, store)).workflows
                    if workflow.name == name
                ]
                if len(workflows) != 1:
                    raise ValueError(f"Expected exactly one enabled workflow: {name}")
                workflow = workflows[0]
                if workflow.requires_approval or workflow.allowed_tenants is not None:
                    raise ValueError(
                        "Direct evaluation is limited to unrestricted read-only workflows"
                    )
                if workflow.runnable is None:
                    raise ValueError("Direct workflow evaluation requires a local runnable")
                result = await workflow.runnable.ainvoke(payload, config=config)
                if workflow.result_schema:
                    workflow.result_schema.model_validate(result.get("structured_response"))
            else:
                overrides = {target.prompt_key: prompt} if prompt is not None else None
                async with aopen_agent(isolated, store=store, prompt_overrides=overrides) as agent:
                    result = await agent.ainvoke(payload, config=config, context=context)
            return normalize_outputs(result, inputs=payload)

    return predict
