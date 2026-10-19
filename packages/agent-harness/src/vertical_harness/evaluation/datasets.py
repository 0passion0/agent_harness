"""JSONL / LangSmith 数据集统一为一次评测中固定的样本快照。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from langsmith import Client
from langsmith.schemas import Example
from langsmith.utils import LangSmithConflictError, LangSmithNotFoundError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vertical_harness.evaluation.scoring import input_payload

logger = logging.getLogger(__name__)


class RegressionCase(BaseModel):
    """单个回归评测用例模型，包含输入 payload、期望输出及元数据。"""

    model_config = ConfigDict(extra="forbid")

    # 评测输入字典（必须包含可解析的消息列表或文本）
    inputs: dict
    # 期望输出字典（可选包含 reference_answer, required_tools 等）
    outputs: dict = Field(default_factory=dict)
    # 用例元数据字典
    metadata: dict = Field(default_factory=dict)
    # 样本在外部数据库或数据集中的唯一标识符
    id: UUID | None = None
    # 关联的数据集唯一标识符
    dataset_id: UUID | None = None

    @model_validator(mode="after")
    def validate_case(self) -> RegressionCase:
        """校验用例输入载荷合法性及所需工具列表格式。

        :return: 校验合法的当前 RegressionCase 实例。
        :rtype: RegressionCase
        :raises ValueError: 当输入不合法或工具要求非字符串列表时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug("validate_regression_case_started")
        # 第一步：前置解析输入载荷并审计必需工具字段合法性
        input_payload(self.inputs)
        for key in ("required_tools", "required_successful_tools"):
            if key in self.outputs and (
                not isinstance(self.outputs[key], list)
                or any(not isinstance(tool, str) for tool in self.outputs[key])
            ):
                raise ValueError(f"{key} must be a list of tool names")
        return self

    @property
    def input_digest(self) -> str:
        """根据标准规范化后的输入载荷计算 SHA-256 摘要哈希值。

        :return: 64 位十六进制哈希字符串。
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：序列化规范输入并计算 SHA-256 摘要
        data = json.dumps(input_payload(self.inputs), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()

    def to_example(self) -> Example:
        """转换为 LangSmith 原生 `Example` 对象。

        :return: 包装后的 Example 实例。
        :rtype: langsmith.schemas.Example
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：派生 UUID 标识并构造 LangSmith Example
        example_id = self.id or uuid5(NAMESPACE_URL, self.model_dump_json(exclude={"id", "dataset_id"}))
        logger.debug(
            "regression_case_to_example_converted",
            extra={"event_fields": {"has_custom_id": self.id is not None}},
        )
        return Example(
            id=example_id,
            dataset_id=self.dataset_id or UUID(int=0),
            inputs=self.inputs,
            outputs=self.outputs,
            metadata=self.metadata,
        )


def load_cases(
    *, path: Path | None = None, dataset: str | None = None, client: Client | None = None
) -> list[RegressionCase]:
    """从本地 JSONL 文件或 LangSmith 远端数据集加载回归测试用例列表。

    :param path: 本地 JSONL 文件路径。
    :type path: pathlib.Path | None
    :param dataset: 远端 LangSmith 数据集名称。
    :type dataset: str | None
    :param client: LangSmith Client 客户端。
    :type client: langsmith.Client | None
    :return: RegressionCase 用例列表。
    :rtype: list[RegressionCase]
    :raises ValueError: 当参数冲突、未提供客户端或数据集为空时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "load_cases_started",
        extra={
            "event_fields": {
                "from_file": path is not None,
                "from_dataset": dataset is not None,
            }
        },
    )
    # 第一步：校验入参排他性
    if (path is None) == (dataset is None):
        raise ValueError("Provide exactly one JSONL file or LangSmith dataset")

    # 第二步：从本地文件或远端客户端拉取用例
    if path is not None:
        cases = [
            RegressionCase.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        if client is None:
            raise ValueError("LangSmith dataset loading requires a client")
        cases = [
            RegressionCase(
                id=example.id,
                inputs=example.inputs,
                outputs=example.outputs or {},
                metadata=example.metadata or {},
                dataset_id=example.dataset_id,
            )
            for example in client.list_examples(dataset_name=dataset)
        ]

    # 第三步：确保数据集非空并返回
    if not cases:
        raise ValueError("Evaluation dataset is empty")
    logger.info(
        "load_cases_completed",
        extra={"event_fields": {"case_count": len(cases)}},
    )
    return cases


def ensure_disjoint(training: list[RegressionCase], validation: list[RegressionCase]) -> None:
    """确保训练集与验证集的数据完全互斥不重叠（基于 input_digest 哈希校验）。

    :param training: 训练集用例列表。
    :type training: list[RegressionCase]
    :param validation: 留出验证集用例列表。
    :type validation: list[RegressionCase]
    :return: 无返回值。
    :rtype: None
    :raises ValueError: 当训练集与验证集存在输入交集时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug(
        "ensure_disjoint_checked",
        extra={"event_fields": {"train_count": len(training), "val_count": len(validation)}},
    )
    # 第一步：计算输入哈希交集并断言隔离
    if {case.input_digest for case in training} & {case.input_digest for case in validation}:
        raise ValueError(
            "Training and validation inputs overlap; provide a held-out validation set"
        )


def get_or_create_dataset(client: Client, name: str) -> Any:
    """按名称获取已有 LangSmith 数据集，若不存在则创建。

    :param client: LangSmith Client 客户端。
    :type client: langsmith.Client
    :param name: 数据集名称。
    :type name: str
    :return: 获取或新建的 Dataset 对象。
    :rtype: typing.Any
    :raises ValueError: 当数据集名称为空时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("get_or_create_dataset_started", extra={"event_fields": {"dataset_name": name}})
    # 第一步：校验数据集名称非空
    if not name.strip():
        raise ValueError("Dataset name cannot be empty")

    # 第二步：尝试读取或创建 LangSmith 数据集
    try:
        return client.read_dataset(dataset_name=name)
    except LangSmithNotFoundError:
        try:
            return client.create_dataset(dataset_name=name)
        except LangSmithConflictError:
            return client.read_dataset(dataset_name=name)


async def experiment_examples(
    client: Client, cases: list[RegressionCase], upload: bool
) -> list[Example]:
    """将评测用例转化为实验样本 Example 列表，并在开启 upload 时上传到快照数据集。

    :param client: LangSmith Client 客户端。
    :type client: langsmith.Client
    :param cases: 回归用例列表。
    :type cases: list[RegressionCase]
    :param upload: 是否向 LangSmith 上传快照。
    :type upload: bool
    :return: 准备好的 Example 列表。
    :rtype: list[langsmith.schemas.Example]
    :raises ValueError: 当远端快照数据已被篡改发生不一致时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "experiment_examples_preparing",
        extra={"event_fields": {"case_count": len(cases), "upload": upload}},
    )
    # 第一步：若未开启上传直接本地转换 Example 列表
    examples = [case.to_example() for case in cases]
    if not upload:
        return examples

    # 第二步：计算不可变内容摘要并获取或创建远端快照数据集
    # 每次上传都创建内容寻址的不可变快照，基线与候选共享同一份参考数据。
    snapshot = "\n".join(case.model_dump_json(exclude={"id", "dataset_id"}) for case in cases)
    digest = hashlib.sha256(snapshot.encode()).hexdigest()
    dataset = await asyncio.to_thread(get_or_create_dataset, client, f"va-eval-{digest[:20]}")
    uploaded = []

    # 第三步：幂等上传并强校验数据一致性
    for case in cases:
        example_id = uuid5(
            NAMESPACE_URL, f"{dataset.id}:{case.model_dump_json(exclude={'id', 'dataset_id'})}"
        )
        try:
            example = await asyncio.to_thread(client.read_example, example_id)
        except LangSmithNotFoundError:
            try:
                example = await asyncio.to_thread(
                    client.create_example,
                    dataset_id=dataset.id,
                    example_id=example_id,
                    inputs=case.inputs,
                    outputs=case.outputs,
                    metadata=case.metadata,
                )
            except LangSmithConflictError:
                example = await asyncio.to_thread(client.read_example, example_id)
        if example.inputs != case.inputs or (example.outputs or {}) != case.outputs:
            raise ValueError(
                "LangSmith snapshot contents changed; refusing an inconsistent comparison"
            )
        uploaded.append(example)
    logger.info(
        "experiment_examples_uploaded",
        extra={"event_fields": {"uploaded_count": len(uploaded)}},
    )
    return uploaded
