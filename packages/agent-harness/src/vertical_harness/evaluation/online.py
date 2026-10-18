"""独立在线评测 worker：稳定抽样、逐 run 隔离失败、幂等反馈，避免阻塞请求。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from langsmith import Client, tracing_context
from langsmith.utils import LangSmithConflictError

from vertical_harness.config import HarnessSettings
from vertical_harness.evaluation.config import EvaluationSettings
from vertical_harness.evaluation.feedback import USER_FEEDBACK_KEY, promote_feedback
from vertical_harness.evaluation.scoring import (
    QUALITY_RUBRIC,
    deterministic_scores,
    judge_payload,
    make_judge,
    normalize_outputs,
)
from vertical_harness.plugins import BuildServices, discover_plugins

logger = logging.getLogger(__name__)


def sampled(run_id: str, rate: float) -> bool:
    """基于 run_id 的 SHA-256 哈希值实现确定性、无偏伪随机抽样。

    :param run_id: 运行记录标识符字符串。
    :param rate: 采样率（0.0 - 1.0）。
    :return: 若该 run 命中抽样区间则返回 True，否则返回 False。
    """
    is_sampled = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:8], "big") / 2**64 < rate
    logger.debug(
        "run_sampling_checked",
        extra={"event_fields": {"rate": rate, "sampled": is_sampled}},
    )
    return is_sampled


async def project_runs(
    client: Client,
    project: str,
    *,
    since: datetime,
    names: list[str] | None = None,
    feedback_changed: bool = False,
):
    """异步流式查询项目中符合时间与名称筛选条件的已完成运行记录（自动分页）。

    :param client: LangSmith 客户端。
    :param project: LangSmith 项目名称。
    :param since: 起始时间窗口。
    :param names: 可选的智能体/工作流名称过滤列表。
    :param feedback_changed: 是否根据反馈修改时间而非运行开始时间进行筛选。
    :yields: 符合条件的已完成 Run 对象。
    """
    logger.debug(
        "querying_project_runs",
        extra={"event_fields": {"project": project, "feedback_changed": feedback_changed}},
    )
    project_info = await asyncio.to_thread(client.read_project, project_name=project)
    resource = await asyncio.to_thread(lambda: client.runs)
    filters = [f"eq(name,{json.dumps(name)})" for name in names or []]
    options = {"min_feedback_modified_at" if feedback_changed else "min_start_time": since}
    if filters:
        options["filter"] = f"or({','.join(filters)})" if len(filters) > 1 else filters[0]
    # SDK 自动分页，不截断到最新一页；以已完成的 trace 为评测对象。
    async for run in resource.query(
        project_ids=[str(project_info.id)],
        run_type="CHAIN",
        page_size=100,
        selects=[
            "ID",
            "NAME",
            "INPUTS",
            "OUTPUTS",
            "ERROR",
            "START_TIME",
            "END_TIME",
            "FEEDBACK_STATS",
            "PROJECT_ID",
        ],
        **options,
    ):
        if run.id and run.end_time:
            yield run


async def evaluate_online_once(
    settings: HarnessSettings, evaluation: EvaluationSettings, client: Client, *, project: str
) -> dict:
    """执行一次在线评测扫描：对窗口内的已完成运行进行抽样评测，并异步写入评估反馈。

    :param settings: 宿主系统配置实例。
    :param evaluation: 评测配置实例。
    :param client: LangSmith 客户端。
    :param project: LangSmith 追踪项目名称。
    :return: 包含 evaluated、skipped、errors 计数的汇总字典。
    :raises ValueError: 当未启用在线评测配置时抛出。
    """
    logger.info("evaluate_online_once_started", extra={"event_fields": {"project": project}})
    if not evaluation.enable_online_evaluation:
        raise ValueError("Enable VA_ENABLE_ONLINE_EVALUATION before starting the online worker")
    names = ["vertical-agent"]
    for plugin in discover_plugins(settings.enabled_plugins):
        names.extend(
            workflow.name
            for workflow in plugin.build(
                BuildServices(
                    settings.resolved_workspace_root, plugin_config=settings.plugin_config
                )
            ).workflows
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                QUALITY_RUBRIC,
                evaluation.eval_model or settings.model,
                settings.base_url,
                sorted(names),
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()[:12]
    key = f"va_online_quality_{fingerprint}"
    judge = None
    counts = {"evaluated": 0, "skipped": 0, "errors": 0}
    since = datetime.now(UTC) - timedelta(hours=evaluation.online_eval_lookback_hours)
    # shortcut: 仅回看配置窗口内开始的 run，长时间停机后的回补需扩大 lookback 后 --once。
    async for run in project_runs(client, project, since=since, names=names):
        try:
            if not sampled(str(run.id), evaluation.online_eval_sample_rate):
                counts["skipped"] += 1
                continue
            if key in (run.feedback_stats or {}) or await asyncio.to_thread(
                lambda run_id=run.id: next(
                    iter(client.list_feedback(run_ids=[run_id], feedback_key=[key], limit=1)), None
                )
            ):
                counts["skipped"] += 1
                continue
            with tracing_context(enabled=False):
                outputs = normalize_outputs(run.outputs or {}, inputs=run.inputs)
                if not deterministic_scores(outputs, None, error=run.error)[0]["score"]:
                    result = {"score": 0, "comment": "Failed, interrupted, or empty run"}
                else:
                    judge = judge or make_judge(settings, evaluation)
                    result = await judge(
                        inputs=judge_payload(run.inputs),
                        outputs=judge_payload(outputs),
                        reference_outputs="{}",
                    )
                score = result.get("score")
                if not isinstance(score, (int, float)) or not 0 <= score <= 1:
                    raise ValueError("Online judge returned an invalid score")
                await asyncio.to_thread(
                    client.create_feedback,
                    run_id=run.id,
                    key=key,
                    score=score,
                    comment=result.get("comment"),
                    feedback_source_type="model",
                    feedback_id=uuid5(NAMESPACE_URL, f"{run.id}:{key}"),
                    source_info={
                        "model": evaluation.eval_model or settings.model,
                        "rubric": fingerprint,
                    },
                    session_id=run.project_id,
                    start_time=run.start_time,
                )
            counts["evaluated"] += 1
        except LangSmithConflictError:
            counts["skipped"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("online_evaluation_failed")
    logger.info("evaluate_online_once_completed", extra={"event_fields": counts})
    return counts


async def sync_feedback_dataset(
    client: Client, *, project: str, dataset: str, since: datetime
) -> dict:
    """将时间窗口内发生更新的用户修正反馈同步提升至指定的回归测试集。

    :param client: LangSmith 客户端。
    :param project: LangSmith 项目名称。
    :param dataset: 目标回归测试集名称。
    :param since: 起始筛选时间。
    :return: 包含 promoted、skipped、errors 的处理统计字典。
    """
    logger.info(
        "sync_feedback_dataset_started",
        extra={"event_fields": {"project": project, "dataset": dataset}},
    )
    counts = {"promoted": 0, "skipped": 0, "errors": 0}
    async for run in project_runs(client, project, since=since, feedback_changed=True):
        try:
            feedbacks = await asyncio.to_thread(
                lambda run_id=run.id: list(
                    client.list_feedback(run_ids=[run_id], feedback_key=[USER_FEEDBACK_KEY])
                )
            )
            for feedback in feedbacks:
                if not feedback.correction:
                    counts["skipped"] += 1
                    continue
                await promote_feedback(client, feedback.id, dataset, project=project)
                counts["promoted"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("feedback_regression_failed")
    logger.info("sync_feedback_dataset_completed", extra={"event_fields": counts})
    return counts


async def run_online_worker(
    settings: HarnessSettings,
    evaluation: EvaluationSettings,
    client: Client,
    *,
    project: str,
    once: bool = False,
) -> dict:
    """运行独立的在线抽样评测后台服务 Worker，定期轮询并评估生产链路中的真实运行轨迹。

    :param settings: 宿主系统配置实例。
    :param evaluation: 评测配置实例。
    :param client: LangSmith 客户端。
    :param project: 目标追踪项目名称。
    :param once: 若为 True 则仅执行单次轮询后立即返回，适用于 CRON 调度器。
    :return: 评测及同步结果字典。
    :raises ValueError: 当未启用在线评测配置时抛出。
    """
    logger.info(
        "online_eval_worker_started",
        extra={"event_fields": {"project": project, "once": once}},
    )
    if not evaluation.enable_online_evaluation:
        raise ValueError("Enable VA_ENABLE_ONLINE_EVALUATION before starting the online worker")
    while True:
        try:
            result = await evaluate_online_once(settings, evaluation, client, project=project)
            if evaluation.enable_feedback_regression:
                assert evaluation.regression_dataset is not None
                result["regression"] = await sync_feedback_dataset(
                    client,
                    project=project,
                    dataset=evaluation.regression_dataset,
                    since=datetime.now(UTC)
                    - timedelta(hours=evaluation.online_eval_lookback_hours),
                )
            logger.info("online_evaluation_completed", extra={"event_fields": result})
            if once:
                return result
        except Exception:
            if once:
                raise
            logger.exception("online_evaluation_poll_failed")
        await asyncio.sleep(evaluation.online_eval_poll_seconds)
