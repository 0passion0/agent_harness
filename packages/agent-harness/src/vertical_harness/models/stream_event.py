"""可直接 JSON 序列化并投递至前端消费的公共流式事件数据契约模块。

本模块定义了与前端 Desktop 客户端通信的标准化流式事件模型 `StreamEvent` 及事件枚举类型 `StreamEventType`。
通过强类型、严格字段校验（`extra="forbid"`）与冻结只读（`frozen=True`）保证跨进程 IPC 数据传递的一致性。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

# 投影至客户端的前端流式事件分类类型
StreamEventType = Literal[
    "run.started",
    "run.completed",
    "run.interrupted",
    "run.failed",
    "chain.started",
    "chain.completed",
    "chain.failed",
    "model.started",
    "model.delta",
    "model.completed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "custom.progress",
]


class StreamEvent(BaseModel):
    """一次执行内 seq 递增；operation_id / parent_ids 标识并行、嵌套调用。

    Attributes:
        type (StreamEventType): 流式事件的具体类别枚举值（如 `"model.delta"`, `"tool.started"`）。
        run_id (str): 单次执行任务的唯一运行时追踪 UUID 字符串。
        thread_id (str): 当前所属对话或状态图会话线程唯一标识符。
        seq (int): 单次运行内单调严格自增的事件序号（从 1 开始），供客户端检测丢包与重排序。
        operation_id (str): 当前生命周期操作片段的唯一标识符（对应 LangChain run_id）。
        parent_ids (list[str]): 父级及所有祖先调用操作 ID 列表，用于在 UI 渲染完整的嵌套调用调用树。
        name (str): 当前触发事件的组件或工具名称（例如 `"shell"`, `"edit_file"`）。
        agent_name (str | None): 触发事件的智能体角色名称（如 `"vertical-agent"` 或子智能体名）。
        node (str | None): LangGraph 状态图内部的节点名称。
        data (dict[str, JsonValue]): 该事件携带的具体结构化数据载荷，例如文本增量或返回码。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # 事件分类类型
    type: StreamEventType
    # 单次运行唯一标识符
    run_id: str
    # 所属对话会话线程标识符
    thread_id: str
    # 运行内严格单调递增的序号，便于客户端检测丢包与重排
    seq: int
    # 当前操作或调用的唯一标识符
    operation_id: str
    # 祖先操作的调用 ID 列表，用于前端还原调用调用树
    parent_ids: list[str]
    # 事件或工具名称
    name: str
    # 触发当前事件的智能体名称
    agent_name: str | None = None
    # LangGraph 状态图内部节点名称
    node: str | None = None
    # 事件数据载荷字典
    data: dict[str, JsonValue]
