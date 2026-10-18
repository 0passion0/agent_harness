"""JSONL / LangSmith 数据集统一为一次评测中固定的样本快照。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from langsmith import Client
from langsmith.schemas import Example
from langsmith.utils import LangSmithConflictError, LangSmithNotFoundError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vertical_harness.evaluation.scoring import input_payload


class RegressionCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: dict
    outputs: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    id: UUID | None = None
    dataset_id: UUID | None = None

    @model_validator(mode="after")
    def validate_case(self) -> RegressionCase:
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
        data = json.dumps(input_payload(self.inputs), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()

    def to_example(self) -> Example:
        return Example(
            id=self.id or uuid5(NAMESPACE_URL, self.model_dump_json(exclude={"id", "dataset_id"})),
            dataset_id=self.dataset_id or UUID(int=0),
            inputs=self.inputs,
            outputs=self.outputs,
            metadata=self.metadata,
        )


def load_cases(
    *, path: Path | None = None, dataset: str | None = None, client: Client | None = None
) -> list[RegressionCase]:
    if (path is None) == (dataset is None):
        raise ValueError("Provide exactly one JSONL file or LangSmith dataset")
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
    if not cases:
        raise ValueError("Evaluation dataset is empty")
    return cases


def ensure_disjoint(training: list[RegressionCase], validation: list[RegressionCase]) -> None:
    if {case.input_digest for case in training} & {case.input_digest for case in validation}:
        raise ValueError(
            "Training and validation inputs overlap; provide a held-out validation set"
        )


def get_or_create_dataset(client: Client, name: str):
    if not name.strip():
        raise ValueError("Dataset name cannot be empty")
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
    examples = [case.to_example() for case in cases]
    if not upload:
        return examples
    # 每次上传都创建内容寻址的不可变快照，基线与候选共享同一份参考数据。
    snapshot = "\n".join(case.model_dump_json(exclude={"id", "dataset_id"}) for case in cases)
    digest = hashlib.sha256(snapshot.encode()).hexdigest()
    dataset = await asyncio.to_thread(get_or_create_dataset, client, f"va-eval-{digest[:20]}")
    uploaded = []
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
    return uploaded
