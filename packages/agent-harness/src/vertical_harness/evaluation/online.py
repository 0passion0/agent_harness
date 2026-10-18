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
    return int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:8], "big") / 2**64 < rate


async def project_runs(
    client: Client,
    project: str,
    *,
    since: datetime,
    names: list[str] | None = None,
    feedback_changed: bool = False,
):
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
            logger.exception(
                "online_evaluation_failed", extra={"event_fields": {"run_id": str(run.id)}}
            )
    return counts


async def sync_feedback_dataset(
    client: Client, *, project: str, dataset: str, since: datetime
) -> dict:
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
            logger.exception(
                "feedback_regression_failed", extra={"event_fields": {"run_id": str(run.id)}}
            )
    return counts


async def run_online_worker(
    settings: HarnessSettings,
    evaluation: EvaluationSettings,
    client: Client,
    *,
    project: str,
    once: bool = False,
) -> dict:
    if not evaluation.enable_online_evaluation:
        raise ValueError("Enable VA_ENABLE_ONLINE_EVALUATION before starting the online worker")
    while True:
        try:
            result = await evaluate_online_once(settings, evaluation, client, project=project)
            if evaluation.enable_feedback_regression:
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
