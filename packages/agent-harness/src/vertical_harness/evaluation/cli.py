"""独立评测入口；参数解析与模块业务逻辑分离。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from vertical_harness.config import HarnessSettings
from vertical_harness.evaluation.config import EvaluationSettings
from vertical_harness.evaluation.datasets import load_cases
from vertical_harness.evaluation.feedback import (
    ReferenceAnswer,
    UserFeedback,
    promote_feedback,
    submit_feedback,
)
from vertical_harness.evaluation.offline import evaluate_offline, optimize_prompt
from vertical_harness.evaluation.online import run_online_worker, sync_feedback_dataset
from vertical_harness.evaluation.targets import EvaluationTarget
from vertical_harness.logging import configure_logging
from vertical_harness.observability import LangSmithSettings

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """构建评测命令行工具的参数解析器。

    :return: 配置好子命令（run, optimize, online, feedback, regression, sync-feedback）的 ArgumentParser 实例。
    """
    logger.debug("build_evaluation_parser_started")
    parser = argparse.ArgumentParser(
        description="LangSmith offline/online evaluation, feedback and regression datasets"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Evaluate main, plugin or deterministic workflow")
    optimize = commands.add_parser(
        "optimize", help="Generate a candidate prompt and compare on held-out cases"
    )
    for command in (run, optimize):
        data = command.add_mutually_exclusive_group(required=True)
        data.add_argument("--data", type=Path, help="Local JSONL cases")
        data.add_argument("--dataset", help="LangSmith dataset name")
        command.add_argument("--target", default="main", help="main, plugin:<id>, workflow:<name>")
        command.add_argument(
            "--upload", action="store_true", help="Upload a dataset snapshot, experiment and traces"
        )
    run.add_argument(
        "--no-judge",
        action="store_true",
        help="Only deterministic checks; workflows can run without LLM calls",
    )
    run.add_argument("--report", type=Path, required=True)
    validation = optimize.add_mutually_exclusive_group(required=True)
    validation.add_argument("--validation-data", type=Path)
    validation.add_argument("--validation-dataset")
    optimize.add_argument(
        "--output-dir", type=Path, required=True, help="New directory for candidate and reports"
    )
    online = commands.add_parser("online", help="Run an independent sampled evaluation worker")
    online.add_argument(
        "--once", action="store_true", help="One poll, suitable for a system scheduler"
    )
    feedback = commands.add_parser("feedback", help="Attach user feedback to a traced run")
    feedback.add_argument("--run-id", type=UUID, required=True)
    feedback.add_argument("--score", type=float, required=True, help="0..1")
    feedback.add_argument("--comment")
    feedback.add_argument(
        "--correction", type=Path, help="JSON reference answer for a regression case"
    )
    feedback.add_argument("--feedback-id", type=UUID)
    promote = commands.add_parser(
        "regression", help="Promote one corrected user feedback into a dataset"
    )
    promote.add_argument("--feedback-id", type=UUID, required=True)
    promote.add_argument("--dataset", required=True)
    sync = commands.add_parser(
        "sync-feedback", help="Import corrected feedback modified within the lookback"
    )
    sync.add_argument("--dataset", required=True)
    sync.add_argument("--hours", type=int, default=168)
    return parser


async def _execute(
    args: argparse.Namespace,
    settings: HarnessSettings | None,
    evaluation: EvaluationSettings,
    smith: LangSmithSettings,
    client: Any,
) -> dict[str, Any]:
    """根据解析后的命令行参数分发并执行具体的评测或反馈工作流。

    :param args: 命令行解析得到的命名空间对象。
    :param settings: 宿主系统配置实例（若执行命令需要）。
    :param evaluation: 评测专门配置实例。
    :param smith: LangSmith 追踪配置实例。
    :param client: LangSmith Client 客户端。
    :return: 执行结果字典。
    :raises FileExistsError: 当输出报告已存在时抛出。
    :raises ValueError: 当参数值不合法时抛出。
    """
    logger.info(
        "evaluation_command_dispatching",
        extra={"event_fields": {"command": args.command}},
    )
    if args.command in {"run", "optimize"}:
        cases = await asyncio.to_thread(
            load_cases, path=args.data, dataset=args.dataset, client=client
        )
        target = EvaluationTarget(args.target)
        if args.command == "optimize":
            validation = await asyncio.to_thread(
                load_cases,
                path=args.validation_data,
                dataset=args.validation_dataset,
                client=client,
            )
            return await optimize_prompt(
                settings,  # type: ignore[arg-type]
                evaluation,
                client,
                cases,
                validation,
                target=target,
                output_dir=args.output_dir,
                upload=args.upload,
            )
        if args.report.exists():
            raise FileExistsError("Report already exists")
        result = await evaluate_offline(
            settings,  # type: ignore[arg-type]
            evaluation,
            client,
            cases,
            target=target,
            judge=not args.no_judge,
            upload=args.upload,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as report:
            json.dump(result, report, ensure_ascii=False, indent=2)
        return result
    if args.command == "online":
        return await run_online_worker(
            settings, evaluation, client, project=smith.project, once=args.once  # type: ignore[arg-type]
        )
    if args.command == "feedback":
        correction = (
            ReferenceAnswer.model_validate_json(args.correction.read_text(encoding="utf-8"))
            if args.correction
            else None
        )
        feedback = UserFeedback(
            run_id=args.run_id,
            score=args.score,
            comment=args.comment,
            correction=correction,
            feedback_id=args.feedback_id,
        )
        return await submit_feedback(client, feedback, evaluation, project=smith.project)
    if args.command == "regression":
        example_id = await promote_feedback(
            client, args.feedback_id, args.dataset, project=smith.project
        )
        return {"example_id": example_id}
    if args.hours < 1:
        raise ValueError("--hours must be positive")
    return await sync_feedback_dataset(
        client,
        project=smith.project,
        dataset=args.dataset,
        since=datetime.now(UTC) - timedelta(hours=args.hours),
    )


def main(argv: Sequence[str] | None = None) -> None:
    """评测工具主入口函数，处理参数解析、环境初始化与异常退出码。

    :param argv: 命令行参数列表，默认从 sys.argv 获取。
    """
    logger.debug("evaluation_main_invoked")
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        settings = HarnessSettings() if args.command in {"run", "optimize", "online"} else None
        if settings:
            configure_logging(settings)
        evaluation, smith = EvaluationSettings(), LangSmithSettings()
        upload = args.command in {"run", "optimize"} and args.upload
        with closing(smith.create_client(auto_batch_tracing=upload)) as client:
            try:
                result = asyncio.run(_execute(args, settings, evaluation, smith, client))
            finally:
                client.flush()
        summary = {
            key: value
            for key, value in result.items()
            if key not in {"rows", "training", "baseline", "candidate"}
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        reports = [
            result,
            result.get("regression", {}),
            result.get("baseline", {}),
            result.get("candidate", {}),
        ]
        if any(report.get("errors") for report in reports):
            parser.exit(1, "Evaluation has errors; inspect the report.\n")
        if args.command == "run" and result.get("failures"):
            parser.exit(1, "Evaluation checks failed; inspect the report.\n")
    except KeyboardInterrupt:
        parser.exit(130, "Evaluation cancelled.\n")
    except Exception as exc:
        logger.exception("evaluation_command_failed")
        parser.exit(1, f"Evaluation failed ({type(exc).__name__}); see application logs.\n")


if __name__ == "__main__":
    main()
