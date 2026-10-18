"""轻量级交互式命令行运行入口；非生产环境 API 服务。"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from collections.abc import Sequence
from time import perf_counter

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from vertical_harness.config import HarnessSettings, TaskTier
from vertical_harness.engine import WorkflowLoggingHandler, build_agent
from vertical_harness.logging import configure_logging, log_context
from vertical_harness.plugins import RunContext, discover_plugins
from vertical_harness.storage import open_store

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    :return: 配置完成的 ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(description="Deep Agents plugin harness")
    parser.add_argument("prompt", nargs="?", help="Input prompt to send to the agent")
    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="List all enabled domain plugins and their manifests as JSON",
    )
    parser.add_argument("--thread-id", default=None, help="Persistent thread identifier")
    parser.add_argument("--tenant-id", default="demo", help="Tenant isolation identifier")
    parser.add_argument("--user-id", default="demo", help="User isolation identifier")
    parser.add_argument(
        "--task-tier",
        choices=[tier.value for tier in TaskTier],
        default=TaskTier.QUALITY.value,
        help="Task routing tier (economy or quality)",
    )
    return parser


def _print_plugins(settings: HarnessSettings) -> None:
    """发现并打印所有已启用插件的元数据清单（JSON 格式）。

    :param settings: 已加载的宿主配置实例。
    """
    manifests = [
        plugin.manifest.model_dump() for plugin in discover_plugins(settings.enabled_plugins)
    ]
    print(json.dumps(manifests, ensure_ascii=False, indent=2))


def _invoke_prompt(args: argparse.Namespace, settings: HarnessSettings) -> None:
    """打开持久化存储，组装 Deep Agent 并使用 LangChain 原生消息与配置执行单轮调用。

    :param args: 解析后的命令行参数对象。
    :param settings: 已加载的宿主配置实例。
    """
    thread_id = args.thread_id or str(uuid.uuid4())
    run_config = RunnableConfig(
        configurable={"thread_id": thread_id},
        callbacks=[WorkflowLoggingHandler()],
    )
    run_context = RunContext(
        tenant_id=args.tenant_id,
        user_id=args.user_id,
        task_tier=args.task_tier,
    )

    logger.info("agent_run_started", extra={"event_fields": {"task_tier": args.task_tier}})
    with open_store(settings) as store:
        agent = build_agent(settings, store=store)
        result = agent.invoke(
            {"messages": [HumanMessage(content=args.prompt)]},
            config=run_config,
            context=run_context,
        )

    interrupted = "__interrupt__" in result
    logger.log(
        logging.WARNING if interrupted else logging.INFO,
        "agent_run_interrupted" if interrupted else "agent_run_completed",
    )
    print(result["messages"][-1].content)
    if interrupted:
        print("Execution interrupted; resume via LangGraph Command(resume=...) from an approval UI")


def main(argv: Sequence[str] | None = None) -> None:
    """列出已启用的插件或使用稳定的线程标识符调用智能体。

    :param argv: 可选的命令行参数序列，默认读取 sys.argv。
    :return: None
    :raises SystemExit: 当未指定输入 prompt 且未传入 --list-plugins 时退出程序。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.list_plugins and not args.prompt:
        parser.error("Please specify a prompt")

    try:
        settings = HarnessSettings()
        configure_logging(settings)
    except ValidationError as exc:
        # Pydantic 的默认错误文本可能包含配置原值，不直接输出。
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        parser.exit(2, f"Invalid harness configuration fields: {', '.join(fields)}.\n")
    except (ValueError, OSError):
        parser.exit(2, "Invalid harness or logging configuration; check VA_* settings.\n")

    with log_context(str(uuid.uuid4())):
        started = perf_counter()
        logger.info("command_started")
        try:
            if args.list_plugins:
                _print_plugins(settings)
            else:
                _invoke_prompt(args, settings)
        except KeyboardInterrupt:
            logger.warning(
                "command_cancelled",
                extra={
                    "event_fields": {"duration_ms": round((perf_counter() - started) * 1000, 2)}
                },
            )
            parser.exit(130, "Execution cancelled.\n")
        except Exception as exc:
            logger.exception(
                "command_failed",
                extra={
                    "event_fields": {"duration_ms": round((perf_counter() - started) * 1000, 2)}
                },
            )
            parser.exit(1, f"Execution failed ({type(exc).__name__}); see application logs.\n")
        else:
            logger.info(
                "command_completed",
                extra={
                    "event_fields": {"duration_ms": round((perf_counter() - started) * 1000, 2)}
                },
            )


if __name__ == "__main__":
    main()
