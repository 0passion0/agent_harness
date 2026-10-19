"""用户反馈写回与显式修正答案到 Regression Dataset 的可追溯转换。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from langsmith import Client
from langsmith.utils import LangSmithConflictError, LangSmithNotFoundError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vertical_harness.evaluation.config import EvaluationSettings
from vertical_harness.evaluation.datasets import RegressionCase, get_or_create_dataset
from vertical_harness.evaluation.scoring import input_payload

logger = logging.getLogger(__name__)

USER_FEEDBACK_KEY = "user_feedback"


class ReferenceAnswer(BaseModel):
    """只有调用方明确提交的修正答案可进入参考输出，不从评分或评论推断答案。"""

    model_config = ConfigDict(extra="forbid")
    # 期望文本输出
    output: str | None = None
    # 期望结构化输出字典
    structured_response: dict | None = None
    # 期望必须调用的工具名称列表
    required_tools: list[str] | None = None

    @model_validator(mode="after")
    def require_answer(self) -> ReferenceAnswer:
        """校验回归参考答案必须包含 output 或 structured_response 之一。

        :return: 校验合法的当前 ReferenceAnswer 实例。
        :rtype: ReferenceAnswer
        :raises ValueError: 当 output 与 structured_response 均为空时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug(
            "validate_reference_answer_started",
            extra={
                "event_fields": {
                    "has_output": bool(self.output and self.output.strip()),
                    "has_structured": self.structured_response is not None,
                }
            },
        )
        # 第一步：校验 output 或 structured_response 至少包含一个有效答案
        if not (self.output and self.output.strip()) and not self.structured_response:
            raise ValueError("A regression reference requires output or structured_response")
        return self


class UserFeedback(BaseModel):
    """用户提交的反馈实体模型，包含评分、可选评论及显式修正答案。"""

    model_config = ConfigDict(extra="forbid")
    # 关联追踪运行记录 ID
    run_id: UUID
    # 用户评分（0.0 - 1.0）
    score: Annotated[float, Field(ge=0, le=1)]
    # 用户评论文本（最长 10,000 字符）
    comment: Annotated[str, Field(max_length=10_000)] | None = None
    # 显式参考修正答案
    correction: ReferenceAnswer | None = None
    # 反馈记录唯一标识符
    feedback_id: UUID | None = None


def feedback_case(feedback: Any, run: Any) -> RegressionCase:
    """将一条带有显式修正的用户反馈和对应运行记录转换为 RegressionCase 回归测试用例。

    :param feedback: LangSmith 反馈记录对象。
    :type feedback: typing.Any
    :param run: LangSmith 运行记录对象。
    :type run: typing.Any
    :return: 提取构建的 RegressionCase 实例。
    :rtype: vertical_harness.evaluation.datasets.RegressionCase
    :raises ValueError: 当反馈无修正答案或运行未完成时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug(
        "feedback_case_conversion_started",
        extra={"event_fields": {"feedback_key": getattr(feedback, "key", "")}},
    )
    # 第一步：校验反馈修正项与运行完成状态
    if feedback.key != USER_FEEDBACK_KEY or not feedback.correction:
        raise ValueError(
            "Only user feedback with an explicit correction can become a reference case"
        )
    if not run.end_time or run.inputs is None:
        raise ValueError("Feedback requires a completed run with available inputs")
    reference = ReferenceAnswer.model_validate(feedback.correction)

    # 第二步：抽取纯净输入载荷与期望修正答案
    # 只重放调用输入，不将生产内部状态、用户评分或原错误回答喂给新智能体。
    inputs = input_payload(run.inputs)

    # 第三步：构建 RegressionCase 并返回
    case = RegressionCase(
        inputs=inputs,
        outputs=reference.model_dump(exclude_none=True),
        metadata={
            "source_run_id": str(run.id),
            "feedback_id": str(feedback.id),
            "user_score": feedback.score,
            "user_comment": feedback.comment,
            "reference_source": "user_correction",
        },
    )
    logger.info("feedback_case_converted")
    return case


async def promote_feedback(
    client: Client, feedback_id: UUID | str, dataset_name: str, *, project: str
) -> str:
    """将单条修正后的用户反馈提升导入到指定的回归测试集中。

    :param client: LangSmith Client 客户端。
    :type client: langsmith.Client
    :param feedback_id: 反馈标识符。
    :type feedback_id: uuid.UUID | str
    :param dataset_name: 目标回归测试集名称。
    :type dataset_name: str
    :param project: LangSmith 项目名称。
    :type project: str
    :return: 新建或已存在的 Example 唯一标识符字符串。
    :rtype: str
    :raises ValueError: 当反馈不属于指定追踪项目或未关联 run 时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "promote_feedback_started",
        extra={"event_fields": {"dataset_name": dataset_name, "project": project}},
    )
    # 第一步：拉取反馈并验证项目匹配
    feedback = await asyncio.to_thread(client.read_feedback, feedback_id)
    if feedback.run_id is None:
        raise ValueError("Run feedback is required")
    project_info = await asyncio.to_thread(client.read_project, project_name=project)
    if feedback.session_id is not None and str(feedback.session_id) != str(project_info.id):
        raise ValueError("Feedback belongs to another tracing project")

    # 第二步：检索对应的运行记录并转换为回归用例
    resource = await asyncio.to_thread(lambda: client.runs)
    run = await resource.retrieve(str(feedback.run_id), project_id=str(project_info.id))
    case = feedback_case(feedback, run)

    # 第三步：获取或创建数据集，幂等注入 Example 样本
    dataset = await asyncio.to_thread(get_or_create_dataset, client, dataset_name)
    example_id = uuid5(NAMESPACE_URL, f"va-regression:{dataset.id}:{feedback.id}")
    try:
        await asyncio.to_thread(client.read_example, example_id)
    except LangSmithNotFoundError:
        # 同一反馈重复导入或并发导入时，保留首次快照。
        with suppress(LangSmithConflictError):
            await asyncio.to_thread(
                client.create_example,
                dataset_id=dataset.id,
                example_id=example_id,
                inputs=case.inputs,
                outputs=case.outputs,
                metadata=case.metadata,
                source_run_id=feedback.run_id,
            )
    logger.info("promote_feedback_completed")
    return str(example_id)


async def submit_feedback(
    client: Client,
    feedback: UserFeedback,
    evaluation: EvaluationSettings | None = None,
    *,
    project: str,
) -> dict[str, Any]:
    """向 LangSmith 写入用户反馈，并在开启反馈回归机制时自动将其提升至回归测试集。

    :param client: LangSmith Client 客户端。
    :type client: langsmith.Client
    :param feedback: 待提交的 UserFeedback 对象。
    :type feedback: UserFeedback
    :param evaluation: 可选的评测配置实例。
    :type evaluation: vertical_harness.evaluation.config.EvaluationSettings | None
    :param project: LangSmith 项目名称。
    :type project: str
    :return: 包含 feedback_id 及可选 example_id 的结果字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "submit_feedback_started",
        extra={
            "event_fields": {
                "score": feedback.score,
                "has_correction": feedback.correction is not None,
                "project": project,
            }
        },
    )
    # 第一步：检索运行记录并向 LangSmith 创建用户反馈
    project_info = await asyncio.to_thread(client.read_project, project_name=project)
    resource = await asyncio.to_thread(lambda: client.runs)
    run = await resource.retrieve(str(feedback.run_id), project_id=str(project_info.id))
    recorded = await asyncio.to_thread(
        client.create_feedback,
        run_id=feedback.run_id,
        key=USER_FEEDBACK_KEY,
        score=feedback.score,
        comment=feedback.comment,
        correction=feedback.correction.model_dump(exclude_none=True)
        if feedback.correction
        else None,
        feedback_id=feedback.feedback_id,
        source_info={"origin": "user"},
        session_id=project_info.id,
        start_time=run.start_time,
    )
    result: dict[str, Any] = {"feedback_id": str(recorded.id)}

    # 第二步：若开启回归机制且包含修正，自动提升至回归测试集
    if evaluation and evaluation.enable_feedback_regression and feedback.correction:
        assert evaluation.regression_dataset is not None
        result["example_id"] = await promote_feedback(
            client, recorded.id, evaluation.regression_dataset, project=project
        )
    logger.info("submit_feedback_completed")
    return result
