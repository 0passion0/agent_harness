"""基于 LangGraph 的确定性学术引用核查工作流插件实现模块。

本模块遵循三层状态隔离规范：
- `CitationInputState` (`input_schema`)：定义调用方/主智能体需要提供的数据（兼容 `messages` 协议）；
- `CitationInternalState` (`InternalState`)：定义工作流内部节点流转使用的数据（禁止外部直接写入）；
- `CitationOutputState` (`output_schema`)：定义工作流最终返回给主智能体的数据。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, NotRequired

from langchain_core.messages import AIMessage, AnyMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from vertical_plugins.common.contracts import (
    SideEffect,
    WorkflowContribution,
    WorkflowInputState,
    WorkflowMode,
    WorkflowOutputState,
)
from vertical_plugins.research_citation.checker.evaluator import evaluate_references
from vertical_plugins.research_citation.models.check import (
    ReferenceCheckInput,
    ReferenceCheckResult,
)

logger = logging.getLogger(__name__)

# 工作流子任务与 Agent Protocol 图注册唯一标识符
CITATION_WORKFLOW_NAME = "citation_audit"

# 匹配 Markdown JSON 代码块（```json ... ```）的正则表达式
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class CitationInputState(WorkflowInputState):
    """定义调用方需要提供的数据（input_schema）。"""

    # 可选的结构化手稿正文纯文本（若未直接传入则由入口节点从 messages 载荷提取）
    manuscript_text: NotRequired[str]
    # 可选的结构化参考文献条目列表（若未直接传入则由入口节点从 messages 载荷提取）
    bibliography: NotRequired[list[str]]


class CitationOutputState(WorkflowOutputState):
    """定义工作流最终返回的数据（output_schema）。"""

    # 供调用方或主智能体直接读取的结构化核查结果字典
    structured_response: NotRequired[dict[str, Any] | None]


class CitationInternalState(CitationInputState, CitationOutputState):
    """定义工作流内部使用的数据（InternalState，不暴露于 input_schema 与 output_schema）。"""

    # 经入口节点提取并通过 ReferenceCheckInput 严格校验后的内部安全参数字典
    validated_input: NotRequired[dict[str, Any] | None]
    # 入口参数提取或 Pydantic 校验失败时的错误原因描述
    validation_error: NotRequired[str | None]
    # 确定性引用比对节点生成的中间评估结果字典
    evaluation_result: NotRequired[dict[str, Any] | None]


# 规范标准别名导出（InternalState / input_schema / output_schema）
InternalState = CitationInternalState
input_schema = CitationInputState
output_schema = CitationOutputState


def _message_content_to_text(message: AnyMessage | dict[str, Any]) -> str:
    """将 LangChain 消息对象或消息字典安全提取为纯文本。

    :param message: LangChain 消息对象或字典。
    :type message: langchain_core.messages.AnyMessage | dict[str, typing.Any]
    :return: 提取出的纯文本字符串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：安全获取字典 content 键或对象属性
    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    # 第二步：针对字符串、多块列表或未知对象进行规范化展平
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    else:
        text = str(content)
    # 第三步：记录提取文本长度日志并返回
    logger.debug(
        "message_content_extracted",
        extra={"event_fields": {"text_length": len(text)}},
    )
    return text


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    """从任务描述文本中提取 JSON 对象字典（支持纯 JSON、代码块 JSON 或内嵌大括号 JSON）。

    :param text: 待提取的原始文本。
    :type text: str
    :return: 成功解析出的字典，若解析失败则返回 None。
    :rtype: dict[str, typing.Any] | None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：去除文本首尾空白并执行空串短路检查
    stripped = text.strip()
    if not stripped:
        return None

    # 第二步：收集所有可能包含有效 JSON 的候选字符串片段
    candidates: list[str] = [stripped]
    fenced_match = _FENCED_JSON_RE.search(stripped)
    if fenced_match is not None:
        candidates.insert(0, fenced_match.group(1).strip())

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        brace_slice = stripped[first_brace : last_brace + 1]
        if brace_slice not in candidates:
            candidates.append(brace_slice)

    # 第三步：逐一尝试候选片段的反序列化并验证字典类型
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            logger.debug(
                "json_dict_extracted",
                extra={"event_fields": {"key_count": len(loaded)}},
            )
            return loaded
    return None


def parse_and_validate_input(state: CitationInternalState) -> dict[str, Any]:
    """工作流入口节点：从输入状态或消息载荷提取参数并执行严格 Pydantic 校验。

    外部调用方或主智能体无法直接写入 `validated_input` 或 `evaluation_result`；
    所有业务数据必须通过本入口节点完成参数转换与边界校验后方可进入内部状态。

    :param state: 当前工作流状态快照。
    :type state: vertical_plugins.research_citation.workflow.CitationInternalState
    :return: 更新 `validated_input` 与 `validation_error` 的内部状态增量。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info("parse_and_validate_input_started")
    # 第一步：提取 input_schema 显式传递的正文与文献列表
    raw_payload: dict[str, Any] = {}
    if "manuscript_text" in state and state["manuscript_text"] is not None:
        raw_payload["manuscript_text"] = state["manuscript_text"]
    if "bibliography" in state and state["bibliography"] is not None:
        raw_payload["bibliography"] = state["bibliography"]

    # 第二步：若缺少直接参数，回退检查最后一轮 messages 中的 JSON 负载
    if "manuscript_text" not in raw_payload or "bibliography" not in raw_payload:
        messages = state.get("messages") or []
        if messages:
            last_text = _message_content_to_text(messages[-1])
            parsed_json = _extract_json_dict(last_text)
            if parsed_json is not None:
                raw_payload = {**parsed_json, **raw_payload}

    # 第三步：校验必填载荷是否存在，若为空则直接记录校验失败
    if not raw_payload:
        error_msg = (
            "Missing citation check parameters: provide 'manuscript_text' and 'bibliography' "
            "via input_schema fields or a JSON task payload."
        )
        logger.warning("citation_workflow_input_invalid", extra={"event_fields": {"reason": "empty_payload"}})
        return {
            "validated_input": None,
            "validation_error": error_msg,
            "evaluation_result": None,
        }

    # 第四步：执行 Pydantic 强类型 schema 校验
    try:
        validated = ReferenceCheckInput.model_validate(raw_payload)
    except ValidationError as exc:
        logger.warning(
            "citation_workflow_input_invalid",
            extra={"event_fields": {"error_count": len(exc.errors())}},
        )
        return {
            "validated_input": None,
            "validation_error": f"Invalid ReferenceCheckInput payload: {exc}",
            "evaluation_result": None,
        }

    # 第五步：校验成功，构造受保护的 validated_input 字典返回
    logger.info(
        "parse_and_validate_input_completed",
        extra={
            "event_fields": {
                "manuscript_len": len(validated.manuscript_text),
                "bibliography_count": len(validated.bibliography),
            }
        },
    )
    return {
        "validated_input": {
            "manuscript_text": validated.manuscript_text,
            "bibliography": list(validated.bibliography),
        },
        "validation_error": None,
        "evaluation_result": None,
    }


def _route_after_validation(
    state: CitationInternalState,
) -> Literal["evaluate_citations", "finalize_output"]:
    """根据入口节点校验结果决定后续确定性路由分支。

    :param state: 当前内部状态。
    :type state: vertical_plugins.research_citation.workflow.CitationInternalState
    :return: 下一节点名称（'evaluate_citations' 或 'finalize_output'）。
    :rtype: typing.Literal['evaluate_citations', 'finalize_output']
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：根据校验错误标记与载荷有效性判断分支
    if state.get("validation_error") is None and state.get("validated_input") is not None:
        route = "evaluate_citations"
    else:
        route = "finalize_output"
    # 第二步：记录路由跳转决策日志并返回节点标识
    logger.debug(
        "citation_workflow_routed",
        extra={"event_fields": {"next_node": route}},
    )
    return route


def evaluate_citations(state: CitationInternalState) -> dict[str, Any]:
    """工作流核心计算节点：基于内部已校验参数执行确定性引用序号范围与覆盖率评估。

    :param state: 包含 `validated_input` 的内部工作流状态。
    :type state: vertical_plugins.research_citation.workflow.CitationInternalState
    :return: 更新 `evaluation_result` 的内部状态增量。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info("evaluate_citations_started")
    # 第一步：防御性检查内部输入载荷是否存在
    validated_input = state.get("validated_input")
    if validated_input is None:
        logger.error("evaluate_citations_missing_validated_input")
        return {
            "validation_error": "Internal state missing 'validated_input'",
            "evaluation_result": None,
        }
    # 第二步：调用底层比对函数执行学术引用与参考文献比对
    result = evaluate_references(
        manuscript_text=validated_input["manuscript_text"],
        bibliography=validated_input["bibliography"],
    )
    # 第三步：记录完成审计指标并将结果模型转换为 JSON 兼容字典返回
    logger.info(
        "evaluate_citations_completed",
        extra={
            "event_fields": {
                "status": result.status.value,
                "issue_count": len(result.issues),
            }
        },
    )
    return {
        "evaluation_result": result.model_dump(mode="json"),
    }


def finalize_output(state: CitationInternalState) -> dict[str, Any]:
    """工作流出口节点：将内部评估结果封装为符合 `CitationOutputState` 的标准响应。

    :param state: 当前内部工作流状态。
    :type state: vertical_plugins.research_citation.workflow.CitationInternalState
    :return: 仅包含 `messages` 与 `structured_response` 的输出状态字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info("finalize_output_started")
    # 第一步：提取中间评估结果与校验错误
    evaluation_result = state.get("evaluation_result")
    validation_error = state.get("validation_error")
    # 第二步：若存在错误，生成标准错误 payload 与对应的 AIMessage 响应
    if validation_error is not None or evaluation_result is None:
        error_payload: dict[str, Any] = {
            "status": "invalid_input",
            "error": validation_error or "Citation evaluation did not produce a result",
        }
        logger.warning("finalize_output_with_error")
        return {
            "messages": [AIMessage(content=json.dumps(error_payload, ensure_ascii=False))],
            "structured_response": error_payload,
        }

    # 第三步：校验成功，构造经 Pydantic 规范化序列化的成功响应并返回
    verified = ReferenceCheckResult.model_validate(evaluation_result)
    logger.info("finalize_output_success", extra={"event_fields": {"status": verified.status.value}})
    return {
        "messages": [AIMessage(content=verified.model_dump_json())],
        "structured_response": verified.model_dump(mode="json"),
    }


def build_citation_workflow(
    *,
    checkpointer: BaseCheckpointSaver[Any] | bool | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """构建并编译确定性学术引用审计 LangGraph 工作流。

    显式绑定三层状态模式：
    - `state_schema=CitationInternalState` (`InternalState`)
    - `input_schema=CitationInputState`
    - `output_schema=CitationOutputState`

    :param checkpointer: 可选的检查点保存器或父图继承布尔标记。
    :type checkpointer: langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any] | bool | None
    :return: 预编译的 `CompiledStateGraph` 实例。
    :rtype: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "build_citation_workflow_started",
        extra={"event_fields": {"has_checkpointer": checkpointer is not None}},
    )
    # 第一步：创建严格声明三层状态架构的 StateGraph 构造器
    builder: StateGraph[Any, Any, Any, Any] = StateGraph(
        CitationInternalState,
        input_schema=CitationInputState,
        output_schema=CitationOutputState,
    )
    # 第二步：注册入口、评估与出口三类业务处理节点
    builder.add_node("parse_and_validate_input", parse_and_validate_input)
    builder.add_node("evaluate_citations", evaluate_citations)
    builder.add_node("finalize_output", finalize_output)

    # 第三步：配置拓扑连线与分支条件路由
    builder.add_edge(START, "parse_and_validate_input")
    builder.add_conditional_edges(
        "parse_and_validate_input",
        _route_after_validation,
        {
            "evaluate_citations": "evaluate_citations",
            "finalize_output": "finalize_output",
        },
    )
    builder.add_edge("evaluate_citations", "finalize_output")
    builder.add_edge("finalize_output", END)

    # 第四步：编译并命名工作流图实例返回
    compiled = builder.compile(checkpointer=checkpointer, name=CITATION_WORKFLOW_NAME)
    logger.info("build_citation_workflow_compiled")
    return compiled


# 导出模块级预编译 LangGraph 实例，可直接配置于 langgraph.json
graph = build_citation_workflow()


def build_citation_workflow_contribution(
    *,
    mode: WorkflowMode | None = None,
) -> WorkflowContribution:
    """构造 `citation_audit` 工作流插件贡献声明（同时支持 `CompiledSubAgent` 与 `AsyncSubAgent`）。

    :param mode: 可选强制指定的接入模式（默认随宿主 `workflow_mode` 自动解析）。
    :type mode: vertical_plugins.common.contracts.WorkflowMode | None
    :return: 经过三层状态模式校验的 `WorkflowContribution` 实例。
    :rtype: vertical_plugins.common.contracts.WorkflowContribution
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "build_citation_workflow_contribution_started",
        extra={"event_fields": {"mode": str(mode)}},
    )
    # 第一步：构造并返回符合三层状态规范的插件工作流贡献对象
    return WorkflowContribution(
        name=CITATION_WORKFLOW_NAME,
        description=(
            "Execute the deterministic LangGraph numeric citation audit workflow. "
            "Provide a JSON payload with 'manuscript_text' and 'bibliography'."
        ),
        internal_state=CitationInternalState,
        input_schema=CitationInputState,
        output_schema=CitationOutputState,
        runnable=graph,
        graph_id=CITATION_WORKFLOW_NAME,
        mode=mode,
        context_mode="isolated",
        durability="sync",
        effect=SideEffect.READ,
        idempotent=True,
        result_schema=ReferenceCheckResult,
    )


__all__ = [
    "CITATION_WORKFLOW_NAME",
    "CitationInputState",
    "CitationInternalState",
    "CitationOutputState",
    "InternalState",
    "build_citation_workflow",
    "build_citation_workflow_contribution",
    "evaluate_citations",
    "finalize_output",
    "graph",
    "input_schema",
    "output_schema",
    "parse_and_validate_input",
]
