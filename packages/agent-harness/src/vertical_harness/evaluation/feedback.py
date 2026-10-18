"""用户反馈写回与显式修正答案到 Regression Dataset 的可追溯转换。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from langsmith import Client
from langsmith.utils import LangSmithConflictError, LangSmithNotFoundError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vertical_harness.evaluation.config import EvaluationSettings
from vertical_harness.evaluation.datasets import RegressionCase, get_or_create_dataset
from vertical_harness.evaluation.scoring import input_payload

USER_FEEDBACK_KEY = "user_feedback"


class ReferenceAnswer(BaseModel):
    """只有调用方明确提交的修正答案可进入参考输出，不从评分或评论推断答案。"""

    model_config = ConfigDict(extra="forbid")
    output: str | None = None
    structured_response: dict | None = None
    required_tools: list[str] | None = None

    @model_validator(mode="after")
    def require_answer(self) -> ReferenceAnswer:
        if not (self.output and self.output.strip()) and not self.structured_response:
            raise ValueError("A regression reference requires output or structured_response")
        return self


class UserFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    score: Annotated[float, Field(ge=0, le=1)]
    comment: Annotated[str, Field(max_length=10_000)] | None = None
    correction: ReferenceAnswer | None = None
    feedback_id: UUID | None = None


def feedback_case(feedback, run) -> RegressionCase:
    if feedback.key != USER_FEEDBACK_KEY or not feedback.correction:
        raise ValueError(
            "Only user feedback with an explicit correction can become a reference case"
        )
    if not run.end_time or run.inputs is None:
        raise ValueError("Feedback requires a completed run with available inputs")
    reference = ReferenceAnswer.model_validate(feedback.correction)
    # 只重放调用输入，不将生产内部状态、用户评分或原错误回答喂给新智能体。
    inputs = input_payload(run.inputs)
    return RegressionCase(
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


async def promote_feedback(
    client: Client, feedback_id: UUID | str, dataset_name: str, *, project: str
) -> str:
    feedback = await asyncio.to_thread(client.read_feedback, feedback_id)
    if feedback.run_id is None:
        raise ValueError("Run feedback is required")
    project_info = await asyncio.to_thread(client.read_project, project_name=project)
    if feedback.session_id is not None and str(feedback.session_id) != str(project_info.id):
        raise ValueError("Feedback belongs to another tracing project")
    resource = await asyncio.to_thread(lambda: client.runs)
    run = await resource.retrieve(str(feedback.run_id), project_id=str(project_info.id))
    case = feedback_case(feedback, run)
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
    return str(example_id)


async def submit_feedback(
    client: Client,
    feedback: UserFeedback,
    evaluation: EvaluationSettings | None = None,
    *,
    project: str,
) -> dict:
    """API 调用方负责鉴权及 run 所有权；CLI 使用当前 LangSmith 凭据。"""
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
    result = {"feedback_id": str(recorded.id)}
    if evaluation and evaluation.enable_feedback_regression and feedback.correction:
        result["example_id"] = await promote_feedback(
            client, recorded.id, evaluation.regression_dataset, project=project
        )
    return result
