"""评测目标适配层；不修改生产存储、提示词或插件注册。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from uuid import uuid4

from langgraph.store.memory import InMemoryStore

from vertical_harness.config import HarnessSettings
from vertical_harness.engine.callbacks import WorkflowLoggingHandler
from vertical_harness.engine.compose import BASE_PROMPT, aopen_agent
from vertical_harness.engine.plugin_loader import discover_plugins
from vertical_harness.evaluation.scoring import input_payload, normalize_outputs
from vertical_plugins.common.contracts import BuildServices, RunContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationTarget:
    """评测目标适配描述模型；支持 main / plugin:<id> / workflow:<name>。"""

    name: str = "main"

    def __post_init__(self) -> None:
        """校验目标名称格式合法性。

        :return: 无返回值。
        :rtype: None
        :raises ValueError: 当目标名称不符合规范时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug("evaluation_target_init", extra={"event_fields": {"target_name": self.name}})
        # 第一步：校验评测目标名称前缀合法性
        if self.name != "main" and not (
            self.name.startswith(("plugin:", "workflow:")) and self.name.partition(":")[2]
        ):
            raise ValueError("Target must be main, plugin:<id>, or workflow:<name>")

    @property
    def prompt_key(self) -> str:
        """获取用于提示词优化的标识键名。

        :return: 插件 ID 或 'main'。
        :rtype: str
        :raises ValueError: 当目标为确定性工作流时抛出（工作流不支持提示词优化）。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：提取目标所关联的提示词覆盖键名
        if self.name.startswith("workflow:"):
            raise ValueError(
                "Deterministic workflows have no prompt to optimize; evaluate them directly"
            )
        return self.name.partition(":")[2] if self.name.startswith("plugin:") else "main"

    def isolated_settings(self, settings: HarnessSettings, root: Path) -> HarnessSettings:
        """为评测执行构建完全内存隔离、临时工作区且无生产持久化污染的配置副本。

        :param settings: 原始系统配置。
        :type settings: vertical_harness.config.HarnessSettings
        :param root: 临时工作区目录路径。
        :type root: pathlib.Path
        :return: 隔离改造后的 HarnessSettings 实例。
        :rtype: vertical_harness.config.HarnessSettings
        :raises ValueError: 当指定的插件未启用时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug(
            "isolated_settings_creating",
            extra={"event_fields": {"target_name": self.name}},
        )
        # 第一步：过滤启用插件并构建纯内存隔离配置副本
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
                "plugin_config": {
                    key: settings.plugin_config[key]
                    for key in plugins
                    if key in settings.plugin_config
                },
            }
        )

    def baseline_prompt(self, settings: HarnessSettings) -> str:
        """提取目标对应的基线系统提示词文本。

        :param settings: 系统配置实例。
        :type settings: vertical_harness.config.HarnessSettings
        :return: 系统提示词字符串。
        :rtype: str
        :raises ValueError: 当插件未声明任何指令提示词时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug("baseline_prompt_extracting", extra={"event_fields": {"target_name": self.name}})
        # 第一步：根据目标类型在临时工作区提取基线提示词
        if self.prompt_key == "main":
            return BASE_PROMPT
        with TemporaryDirectory(prefix="va-prompt-") as root:
            isolated = self.isolated_settings(settings, Path(root))
            (plugin,) = discover_plugins(isolated.enabled_plugins)
            prompt = plugin.build(
                BuildServices(Path(root), InMemoryStore(), isolated.plugin_config,
                              python_executable=isolated.python_executable)
            ).instructions
        if not prompt.strip():
            raise ValueError("Plugin has no instruction prompt to optimize")
        return prompt


def make_target(settings: HarnessSettings, target: EvaluationTarget, *, prompt: str | None = None) -> Any:
    """根据评测目标类型构造隔离的异步预测执行函数。

    :param settings: 宿主系统配置实例。
    :type settings: vertical_harness.config.HarnessSettings
    :param target: 评测目标对象。
    :type target: EvaluationTarget
    :param prompt: 可选覆盖的提示词字符串。
    :type prompt: str | None
    :return: 异步 predict(inputs) -> dict 预测函数。
    :rtype: typing.Any
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info("make_target_started", extra={"event_fields": {"target_name": target.name}})

    async def predict(inputs: dict[str, Any]) -> dict[str, Any]:
        """执行单次目标评测预测。

        :param inputs: 评测输入字典。
        :type inputs: dict[str, typing.Any]
        :return: 标准化输出与使用统计结果。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        started = perf_counter()
        usage = WorkflowLoggingHandler()
        # 第一步：构建临时目录、内存存储与上下文运行配置
        with TemporaryDirectory(prefix="va-eval-") as directory:
            root = Path(directory)
            isolated = target.isolated_settings(settings, root)
            store = InMemoryStore()
            context = RunContext(tenant_id="evaluation", user_id=str(uuid4()))
            payload = input_payload(inputs)
            config = {
                "configurable": {"thread_id": str(uuid4())},
                "metadata": {"evaluation_target": target.name},
                "callbacks": [usage],
            }
            # 第二步：针对确定性工作流或主智能体分别执行异步调用
            if target.name.startswith("workflow:"):
                name = target.name.partition(":")[2]
                workflows = [
                    workflow
                    for plugin in discover_plugins(isolated.enabled_plugins)
                    for workflow in plugin.build(
                        BuildServices(root, store, isolated.plugin_config,
                                      python_executable=isolated.python_executable)
                    ).workflows
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
                async with asyncio.timeout(settings.run_timeout_seconds):
                    result = await workflow.runnable.ainvoke(payload, config=config)
                if workflow.result_schema:
                    workflow.result_schema.model_validate(result.get("structured_response"))
            else:
                overrides = {target.prompt_key: prompt} if prompt is not None else None
                async with aopen_agent(isolated, store=store, prompt_overrides=overrides) as agent:
                    async with asyncio.timeout(settings.run_timeout_seconds):
                        result = await agent.ainvoke(payload, config=config, context=context)

            # 第三步：标准化输出结果并注入耗时与 Token 使用统计
            normalized = normalize_outputs(result, inputs=payload)
            duration_ms = round((perf_counter() - started) * 1000, 2)
            logger.debug(
                "target_prediction_completed",
                extra={"event_fields": {"target_name": target.name, "duration_ms": duration_ms}},
            )
            return {
                **normalized,
                "usage": {**usage.usage, "duration_ms": duration_ms},
            }

    return predict
