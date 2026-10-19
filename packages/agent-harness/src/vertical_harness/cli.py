"""轻量级交互式命令行运行入口；非生产环境 API 服务。

本模块提供命令行交互界面，支持插件清单查询、工作流契约打印、Time Travel 历史检查点查看、
单次与连续会话调用、分叉调用（Time Travel Fork）以及周期性记忆合并 Worker。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import mimetypes
import uuid
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from vertical_harness.config import HarnessSettings, TaskTier
from vertical_harness.engine import (
    WorkflowLoggingHandler,
    aget_checkpoint_history,
    aopen_agent,
    atime_travel_invoke,
)
from vertical_harness.engine.memory import run_memory_worker
from vertical_harness.logging import configure_logging, log_context
from vertical_harness.observability import tracing_session
from vertical_harness.plugins import BuildServices, RunContext, discover_plugins

logger = logging.getLogger(__name__)


def _image_content(value: str) -> dict[str, str]:
    """读取本地图片为标准内容块，限制读取量以免附件耗尽内存。

    校验图片文件格式（支持 PNG、JPEG、GIF、WebP）及字节大小（<= 20 MiB），编码为 Base64 字典。

    :param value: 本地图片的文件路径字符串。
    :type value: str
    :return: 包含 Base64 编码数据与 MIME 类型的标准多模态内容块字典。
    :rtype: dict[str, str]
    :raises argparse.ArgumentTypeError: 当文件不存在、格式不受支持或大小超出 20 MiB 时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("parsing_image_argument")
    # 第一步：解析文件路径并检测 MIME 类型
    path = Path(value).expanduser()
    mime_type = mimetypes.guess_type(path.name)[0]
    if mime_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        raise argparse.ArgumentTypeError("Image must be PNG, JPEG, GIF or WebP")
    max_bytes = 20 * 1024 * 1024

    # 第二步：安全读取图片字节流并校验文件体积上限
    try:
        with path.open("rb") as source:
            data = source.read(max_bytes + 1)
    except OSError as exc:
        raise argparse.ArgumentTypeError("Cannot read image file") from exc
    if not data or len(data) > max_bytes:
        raise argparse.ArgumentTypeError("Image must be nonempty and at most 20 MiB")

    # 第三步：构建并返回 Base64 编码的多模态图片字典
    logger.debug(
        "image_content_parsed",
        extra={"event_fields": {"mime_type": mime_type, "byte_size": len(data)}},
    )
    return {
        "type": "image",
        "base64": base64.b64encode(data).decode("ascii"),
        "mime_type": mime_type,
    }


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    配置用于交互提示输入、图片附件上传、插件/工作流清单输出、
    Time Travel 历史回溯与分叉执行、记忆合并后台 Worker 运行的命令行选项。

    :return: 配置完成的 ArgumentParser 实例。
    :rtype: argparse.ArgumentParser
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("building_cli_parser")
    # 第一步：初始化主解析器并添加基础交互与图片选项
    parser = argparse.ArgumentParser(description="Deep Agents plugin harness")
    parser.add_argument("prompt", nargs="?", help="Input prompt to send to the agent")
    parser.add_argument(
        "--image",
        dest="images",
        action="append",
        default=[],
        type=_image_content,
        metavar="PATH",
        help="Attach a local image (PNG/JPEG/GIF/WebP, <=20 MiB); repeat for multiple images",
    )

    # 第二步：添加记忆合并互斥命令组
    memory_command = parser.add_mutually_exclusive_group()
    memory_command.add_argument(
        "--memory-worker",
        action="store_true",
        help="Run the independent weekly memory consolidation worker (one per store)",
    )
    memory_command.add_argument(
        "--consolidate-memory",
        action="store_true",
        help="Process due memory consolidations once, suitable for a system scheduler",
    )

    # 第三步：添加插件、工作流与检查点历史查询选项
    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="List all enabled domain plugins and their manifests as JSON",
    )
    parser.add_argument(
        "--list-workflows",
        action="store_true",
        help="List all registered LangGraph workflow plugins and their schemas as JSON",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="List Time Travel checkpoint history for --thread-id as JSON",
    )

    # 第四步：添加 Time Travel 与多租户运行时隔离参数
    parser.add_argument("--thread-id", default=None, help="Persistent thread identifier")
    parser.add_argument(
        "--run-id", type=uuid.UUID, help="LangSmith run UUID for attaching user feedback"
    )
    parser.add_argument(
        "--checkpoint-id",
        default=None,
        help="Historical checkpoint identifier for Time Travel replay or fork",
    )
    parser.add_argument(
        "--fork-thread-id",
        default=None,
        help="Target thread identifier when forking from a checkpoint (Time Travel Fork)",
    )
    parser.add_argument(
        "--update-state",
        default=None,
        help="JSON object string of state updates to apply at the checkpoint fork point",
    )
    parser.add_argument(
        "--as-node",
        default=None,
        help="Optional graph node name to attribute --update-state to",
    )
    parser.add_argument(
        "--durability",
        choices=["sync", "async", "exit"],
        default=None,
        help="LangGraph checkpoint durability mode (default: sync)",
    )
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
    :type settings: HarnessSettings
    :return: None
    :rtype: None
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("printing_plugins_started")
    # 第一步：发现所有已启用的插件并导出清单字典
    manifests = [
        plugin.manifest.model_dump() for plugin in discover_plugins(settings.enabled_plugins)
    ]
    logger.debug(
        "printing_plugins_completed", extra={"event_fields": {"plugin_count": len(manifests)}}
    )
    # 第二步：格式化输出 JSON 字符串
    print(json.dumps(manifests, ensure_ascii=False, indent=2))


def _print_workflows(settings: HarnessSettings) -> None:
    """发现并打印所有已启用插件导出的 LangGraph 工作流元数据与三层状态契约（JSON 格式）。

    :param settings: 已加载的宿主配置实例。
    :type settings: HarnessSettings
    :return: None
    :rtype: None
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("printing_workflows_started")
    # 第一步：初始化构建服务容器
    services = BuildServices(
        workspace_root=settings.resolved_workspace_root, plugin_config=settings.plugin_config
    )
    entries = []

    # 第二步：遍历插件并提取各工作流的声明契约元数据
    for plugin in discover_plugins(settings.enabled_plugins):
        contribution = plugin.build(services)
        for wf in contribution.workflows:
            entries.append(
                {
                    "plugin_id": plugin.manifest.plugin_id,
                    "name": wf.name,
                    "description": wf.description,
                    "mode": wf.resolve_mode(settings.workflow_mode).value,
                    "graph_id": wf.graph_id,
                    "context_mode": wf.context_mode,
                    "durability": wf.durability,
                    "effect": wf.effect.value,
                    "depends_on": list(wf.depends_on),
                    "max_dispatches": wf.max_dispatches,
                    "idempotent": wf.idempotent,
                    "internal_state": getattr(
                        wf.internal_state, "__name__", str(wf.internal_state)
                    ),
                    "input_schema": getattr(wf.input_schema, "__name__", str(wf.input_schema)),
                    "output_schema": getattr(wf.output_schema, "__name__", str(wf.output_schema)),
                }
            )

    # 第三步：记录统计日志并格式化输出 JSON
    logger.debug(
        "printing_workflows_completed", extra={"event_fields": {"workflow_count": len(entries)}}
    )
    print(json.dumps(entries, ensure_ascii=False, indent=2))


def _print_history(args: argparse.Namespace, settings: HarnessSettings) -> None:
    """查询并打印指定线程的 Time Travel 检查点历史摘要（JSON 格式）。

    :param args: 解析后的命令行参数对象。
    :type args: argparse.Namespace
    :param settings: 已加载的宿主配置实例。
    :type settings: HarnessSettings
    :return: None
    :rtype: None
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug(
        "printing_checkpoint_history_started",
        extra={"event_fields": {"thread_id": str(args.thread_id)}},
    )

    # 第一步：定义异步查询任务，在禁用 MCP 连接的隔离配置下查询检查点历史
    async def query():
        # 历史查询需要图与插件生命周期，但无需建立外部 MCP 服务器连接
        async with aopen_agent(settings.model_copy(update={"mcp_servers": {}})) as agent:
            return await aget_checkpoint_history(agent, args.thread_id)

    # 第二步：运行事件循环获取检查点摘要列表
    history = asyncio.run(query())
    logger.debug(
        "printing_checkpoint_history_completed",
        extra={"event_fields": {"checkpoint_count": len(history)}},
    )

    # 第三步：序列化并输出检查点历史
    print(
        json.dumps(
            [item.model_dump() for item in history],
            ensure_ascii=False,
            indent=2,
        )
    )


def _invoke_prompt(args: argparse.Namespace, settings: HarnessSettings) -> None:
    """打开持久化存储与检查点，组装 Deep Agent 并执行常规调用或 Time Travel / Fork 调用。

    :param args: 解析后的命令行参数对象。
    :type args: argparse.Namespace
    :param settings: 已加载的宿主配置实例。
    :type settings: HarnessSettings
    :return: None
    :rtype: None
    :raises ValueError: 当 --update-state 不是合法 JSON 对象字典时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：构建运行时配置与多租户上下文
    thread_id = args.thread_id or str(uuid.uuid4())
    durability = args.durability or settings.durability
    callbacks = [WorkflowLoggingHandler()]
    run_config = RunnableConfig(
        run_id=args.run_id,
        configurable={"thread_id": thread_id},
        callbacks=callbacks,
    )
    run_context = RunContext(
        tenant_id=args.tenant_id,
        user_id=args.user_id,
        task_tier=args.task_tier,
    )

    # 第二步：解析并验证状态更新负载与 Time Travel 分支模式
    update_values = json.loads(args.update_state) if args.update_state is not None else None
    if update_values is not None and not isinstance(update_values, dict):
        raise ValueError("--update-state must be a JSON object")

    is_time_travel = bool(
        args.checkpoint_id is not None
        or args.fork_thread_id is not None
        or update_values is not None
    )

    # 第三步：组装多模态用户输入消息体与调用参数映射
    logger.info("agent_run_started", extra={"event_fields": {"task_tier": args.task_tier}})
    content = (
        ([{"type": "text", "text": args.prompt}] if args.prompt else []) + args.images
        if args.images
        else args.prompt
    )
    payload = {"messages": [HumanMessage(content=content)]} if content else None
    travel_kwargs = {
        "checkpoint_id": args.checkpoint_id,
        "input_state": payload,
        "values": update_values,
        "as_node": args.as_node,
        "fork_thread_id": args.fork_thread_id,
        "context": run_context,
        "callbacks": callbacks,
        "durability": durability,
        "run_id": args.run_id,
    }
    invoke_kwargs = {"config": run_config, "context": run_context, "durability": durability}

    # 第四步：在超时限制与托管生命周期内执行异步图调用
    async def invoke_managed():
        async with asyncio.timeout(settings.run_timeout_seconds), aopen_agent(settings) as agent:
            if is_time_travel:
                return await atime_travel_invoke(agent, thread_id, **travel_kwargs)
            return await agent.ainvoke(payload, **invoke_kwargs)

    result = asyncio.run(invoke_managed())

    # 第五步：处理图中断状态或打印最终输出消息内容
    interrupted = "__interrupt__" in result
    logger.log(
        logging.WARNING if interrupted else logging.INFO,
        "agent_run_interrupted" if interrupted else "agent_run_completed",
    )
    print(result["messages"][-1].content)
    if interrupted:
        print("Execution interrupted; resume via LangGraph Command(resume=...) from an approval UI")


def main(argv: Sequence[str] | None = None) -> None:
    """列出已启用的插件/工作流、查询检查点历史，或使用稳定的线程标识符调用智能体。

    :param argv: 可选的命令行参数序列，默认读取 sys.argv。
    :type argv: Sequence[str] | None
    :return: None
    :rtype: None
    :raises SystemExit: 当参数缺失、冲突或执行异常时退出程序。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：解析命令行参数并进行业务互斥与前置依赖校验
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.images and (
        args.list_plugins
        or args.list_workflows
        or args.history
        or args.memory_worker
        or args.consolidate_memory
    ):
        parser.error("--image cannot be combined with listing, history or memory worker commands")
    if (args.memory_worker or args.consolidate_memory) and (
        args.prompt
        or args.list_plugins
        or args.list_workflows
        or args.history
        or args.checkpoint_id
        or args.fork_thread_id
        or args.update_state
    ):
        parser.error("Memory worker commands cannot be combined with agent commands")
    if args.history and not args.thread_id:
        parser.error("--history requires --thread-id")
    if (args.fork_thread_id or args.checkpoint_id or args.update_state) and not args.thread_id:
        parser.error("Time Travel / Fork options require --thread-id")
    is_time_travel = bool(args.checkpoint_id or args.fork_thread_id or args.update_state)
    if (
        not args.list_plugins
        and not args.list_workflows
        and not args.history
        and not is_time_travel
        and not args.memory_worker
        and not args.consolidate_memory
        and not args.prompt
        and not args.images
    ):
        parser.error("Please specify a prompt or --image")

    # 第二步：加载系统宿主配置并初始化日志系统
    try:
        settings = HarnessSettings()
        configure_logging(settings)
    except ValidationError as exc:
        # Pydantic 的默认错误文本可能包含配置原值，不直接输出。
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        parser.exit(2, f"Invalid harness configuration fields: {', '.join(fields)}.\n")
    except (ValueError, OSError):
        parser.exit(2, "Invalid harness or logging configuration; check VA_* settings.\n")

    # 第三步：在日志会话与链路追踪上下文中分发执行指令
    args.run_id = args.run_id or uuid.uuid4()
    with log_context(str(args.run_id)), tracing_session():
        started = perf_counter()
        logger.info("command_started")
        try:
            if args.memory_worker or args.consolidate_memory:
                run_memory_worker(settings, once=args.consolidate_memory)
            elif args.list_plugins:
                _print_plugins(settings)
            elif args.list_workflows:
                _print_workflows(settings)
            elif args.history:
                _print_history(args, settings)
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
