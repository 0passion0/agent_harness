"""基于 BaseWorkflow 的确定性学术引用核查工作流实现模块。

本模块遵循三层状态隔离规范：
- `CitationInputState` (`input_schema`)：定义调用方/主智能体需要提供的数据（兼容 `messages` 协议）；
- `CitationInternalState` (`InternalState`)：定义工作流内部节点流转使用的数据（禁止外部直接写入）；
- `CitationOutputState` (`output_schema`)：定义工作流最终返回给主智能体的数据。
"""

from __future__ import annotations

import logging
from typing import Any, NotRequired

from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from vertical_plugins.common.contracts import (
    SideEffect,
    WorkflowInputState,
    WorkflowInternalState,
    WorkflowOutputState,
)
from vertical_plugins.common.workflow import (
    BaseWorkflow,
    WorkflowNodeEvent,
    WorkflowPlan,
    extract_json_payload,
    extract_message_text,
)
from vertical_plugins.research_citation.checker.evaluator import evaluate_references
from vertical_plugins.research_citation.models.check import (
    ReferenceCheckInput,
    ReferenceCheckResult,
)

logger = logging.getLogger(__name__)

# 工作流子任务与 Agent Protocol 图注册唯一标识符
CITATION_WORKFLOW_NAME = "citation_audit"


class CitationInputState(WorkflowInputState):
    """定义调用方需要提供的数据（input_schema）。"""

    # 可选的结构化手稿正文纯文本（若未直接传入则由入口节点从 messages 载荷提取）
    manuscript_text: NotRequired[str]
    # 可选的结构化参考文献条目列表（若未直接传入则由入口节点从 messages 载荷提取）
    bibliography: NotRequired[list[str]]


class CitationOutputState(WorkflowOutputState):
    """定义工作流最终返回的数据（output_schema）。"""


class CitationInternalState(WorkflowInternalState):
    """定义工作流内部使用的数据（InternalState，不暴露于 input_schema 与 output_schema）。"""

    # 可选的结构化手稿正文纯文本
    manuscript_text: NotRequired[str]
    # 可选的结构化参考文献条目列表
    bibliography: NotRequired[list[str]]
    # 经入口节点提取并通过 ReferenceCheckInput 严格校验后的内部安全参数字典
    validated_input: NotRequired[dict[str, Any] | None]
    # 入口参数提取或 Pydantic 校验失败时的错误原因描述
    validation_error: NotRequired[str | None]
    # 错误状态分类标识
    error_status: NotRequired[str | None]
    # 确定性引用比对节点生成的中间评估结果字典
    evaluation_result: NotRequired[dict[str, Any] | None]
    # 节点执行事件摘要列表
    workflow_events: NotRequired[list[dict[str, Any]]]


# 规范标准别名导出（InternalState / input_schema / output_schema）
InternalState = CitationInternalState
input_schema = CitationInputState
output_schema = CitationOutputState


class CitationAuditWorkflow(BaseWorkflow):
    """确定性学术引用序号与参考文献列表一致性核查工作流。

    检查范围严格限定为：文稿内数字引用序号、起止范围及与参考文献列表长度的一致性比对；
    不对文献真实性或学术观点的语义支持关系做核验。
    """

    plugin_id = "research.citations"
    workflow_name = CITATION_WORKFLOW_NAME
    description = (
        "Execute the deterministic LangGraph numeric citation audit workflow. "
        "Provide a JSON payload with 'manuscript_text' and 'bibliography'."
    )
    input_schema = CitationInputState
    output_schema = CitationOutputState
    internal_state = CitationInternalState
    result_schema = ReferenceCheckResult
    effect = SideEffect.READ
    idempotent = True

    def run(self) -> WorkflowPlan:
        """声明完整的确定性引用核查三阶段工作流。

        :return: 包含 validate_input、check_citations、build_report 三个独立节点的执行计划。
        :rtype: vertical_plugins.common.workflow.WorkflowPlan
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：声明固定顺序的三个 LangGraph 业务节点
        return self.pipeline(
            self.step(
                "validate_input",
                self.validate_input,
                editable_fields=("manuscript_text", "bibliography"),
                input_model=ReferenceCheckInput,
                description="校验文稿正文与参考文献列表输入参数",
            ),
            self.step(
                "check_citations",
                self.check_citations,
                description="解析数字引用并执行确定性序号与范围一致性比对",
            ),
            self.step(
                "build_report",
                self.build_report,
                description="整理引用异常位置、问题类型及已核验范围输出报告",
            ),
        )

    def validate_input(self, state: CitationInternalState) -> dict[str, Any]:
        """阶段一：从输入状态或消息载荷提取文稿和参考文献列表，并执行严格 Pydantic 校验。

        :param state: 当前工作流状态快照。
        :type state: CitationInternalState
        :return: 更新 `validated_input` 与 `validation_error` 的内部状态增量。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("validate_input_started")
        # 第一步：提取 input_schema 显式传递的正文与文献列表
        raw_payload: dict[str, Any] = {}
        if "manuscript_text" in state and state["manuscript_text"] is not None:
            raw_payload["manuscript_text"] = state["manuscript_text"]
        if "bibliography" in state and state["bibliography"] is not None:
            raw_payload["bibliography"] = state["bibliography"]

        # 第二步：若缺少直接参数，回退提取最后一轮 messages 中的 JSON 负载
        if "manuscript_text" not in raw_payload or "bibliography" not in raw_payload:
            messages = state.get("messages") or []
            if messages:
                last_text = extract_message_text(messages[-1])
                parsed_json = extract_json_payload(last_text)
                if parsed_json is not None:
                    raw_payload = {**parsed_json, **raw_payload}

        # 第三步：校验必填载荷是否存在
        if not raw_payload:
            error_msg = (
                "Missing citation check parameters: provide 'manuscript_text' and 'bibliography' "
                "via input_schema fields or a JSON task payload."
            )
            logger.warning(
                "citation_workflow_input_invalid",
                extra={"event_fields": {"reason": "empty_payload"}},
            )
            return {
                "validated_input": None,
                "validation_error": error_msg,
                "error_status": "invalid_input",
                "evaluation_result": None,
            }

        # 第四步：执行 ReferenceCheckInput 强类型 schema 校验
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
                "error_status": "invalid_input",
                "evaluation_result": None,
            }

        # 第五步：返回受保护的 validated_input 字典
        logger.info(
            "validate_input_completed",
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
            "error_status": None,
            "evaluation_result": None,
        }

    def check_citations(self, state: CitationInternalState) -> dict[str, Any]:
        """阶段二：基于内部已校验参数解析数字引用并执行确定性比对。

        :param state: 包含 `validated_input` 的内部工作流状态。
        :type state: CitationInternalState
        :return: 更新 `evaluation_result` 的内部状态增量。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("check_citations_started")
        # 第一步：核验前置已校验输入载荷
        validated_input = state.get("validated_input")
        if validated_input is None:
            logger.error("check_citations_missing_validated_input")
            return {
                "validation_error": "Internal state missing 'validated_input'",
                "error_status": "invalid_input",
                "evaluation_result": None,
            }

        # 第二步：调用确定性纯函数执行引用序号与参考文献列表比对
        result = evaluate_references(
            manuscript_text=validated_input["manuscript_text"],
            bibliography=validated_input["bibliography"],
        )

        # 第三步：记录审计指标并返回中间评估结果字典
        logger.info(
            "check_citations_completed",
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

    def build_report(self, state: CitationInternalState) -> dict[str, Any]:
        """阶段三：整理异常位置、问题类型及核查范围，输出符合 `CitationOutputState` 的标准响应。

        :param state: 当前内部工作流状态。
        :type state: CitationInternalState
        :return: 包含 `messages` 与 `structured_response` 的输出状态字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("build_report_started")
        # 第一步：检查中间评估结果是否存在
        evaluation_result = state.get("evaluation_result")
        validation_error = state.get("validation_error")
        if validation_error is not None or evaluation_result is None:
            err_msg = validation_error or "Citation evaluation did not produce a result"
            logger.warning("build_report_with_error")
            return self.format_error_output(err_msg, status="invalid_input")

        # 第二步：通过 ReferenceCheckResult 模型二次核验并生成标准输出
        verified = ReferenceCheckResult.model_validate(evaluation_result)
        logger.info("build_report_success", extra={"event_fields": {"status": verified.status.value}})
        return {
            "messages": [AIMessage(content=verified.model_dump_json())],
            "structured_response": verified.model_dump(mode="json"),
        }

    def summarize_node_output(
        self,
        node_name: str,
        state: dict[str, Any],
        update: dict[str, Any],
    ) -> tuple[str, dict[str, Any], tuple[str, ...]]:
        """生成引用核查各阶段的结构化事件摘要与统计指标。

        :param node_name: 节点名称。
        :type node_name: str
        :param state: 节点执行前状态。
        :type state: dict[str, typing.Any]
        :param update: 节点输出增量。
        :type update: dict[str, typing.Any]
        :return: (摘要字符串, 关键指标字典, 产物路径元组)。
        :rtype: tuple[str, dict[str, typing.Any], tuple[str, ...]]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：根据节点名称提取关键核查统计指标
        _ = state
        if node_name == "validate_input":
            val_in = update.get("validated_input")
            if isinstance(val_in, dict):
                bib_count = len(val_in.get("bibliography") or [])
                text_len = len(val_in.get("manuscript_text") or "")
                return (
                    f"Validated manuscript ({text_len} chars) and {bib_count} bibliography entries",
                    {"manuscript_length": text_len, "bibliography_count": bib_count},
                    (),
                )
        elif node_name == "check_citations":
            eval_res = update.get("evaluation_result")
            if isinstance(eval_res, dict):
                issues = eval_res.get("issues") or []
                cited = eval_res.get("cited_indices") or []
                return (
                    f"Checked {len(cited)} cited indices, found {len(issues)} issue(s)",
                    {
                        "status": eval_res.get("status"),
                        "cited_count": len(cited),
                        "issue_count": len(issues),
                    },
                    (),
                )
        elif node_name == "build_report":
            resp = update.get("structured_response")
            if isinstance(resp, dict):
                return (
                    f"Citation audit report ready (status={resp.get('status')})",
                    {"status": resp.get("status"), "verified_scope": resp.get("verified_scope")},
                    (),
                )
        return super().summarize_node_output(node_name, state, update)


_lazy_default_graph: CompiledStateGraph[Any, Any, Any, Any] | None = None


def get_citation_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """按需延迟编译并返回 `langgraph.json` 兼容的引用审计状态图实例。

    :return: 编译后的 CompiledStateGraph 实例。
    :rtype: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：仅在首次访问时编译默认状态图，避免模块导入期副作用
    global _lazy_default_graph
    if _lazy_default_graph is None:
        _lazy_default_graph = CitationAuditWorkflow().compile()
    return _lazy_default_graph


def __getattr__(name: str) -> Any:
    """支持 `langgraph.json` 按属性名 `graph` 延迟加载状态图。

    :param name: 属性名称。
    :type name: str
    :return: 属性值。
    :rtype: typing.Any
    :raises AttributeError: 访问未知属性时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：拦截对模块级 graph 的延迟访问
    if name == "graph":
        return get_citation_graph()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CITATION_WORKFLOW_NAME",
    "CitationAuditWorkflow",
    "CitationInputState",
    "CitationInternalState",
    "CitationOutputState",
    "InternalState",
    "WorkflowNodeEvent",
    "get_citation_graph",
    "input_schema",
    "output_schema",
]
