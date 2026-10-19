"""基于 LangGraph 的确定性科研数据分析与报告校验工作流模块。

本模块遵循三层状态隔离架构契约：
- `DataAnalysisInputState` (`input_schema`)：定义外部调用方需要提供的数据（兼容 messages 协议）；
- `DataAnalysisInternalState` (`InternalState`)：定义工作流内部节点流转专用的数据；
- `DataAnalysisOutputState` (`output_schema`)：定义工作流最终返回的数据。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Literal, NotRequired

from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from data_analysis.models.profile import TableProfileInput
from data_analysis.models.report import ReportValidationInput
from data_analysis.prompts import (
    FINALIZE_OUTPUT_NODE_PROMPT,
    INPUT_VALIDATION_NODE_PROMPT,
    PROFILE_NODE_PROMPT,
    VALIDATE_REPORT_NODE_PROMPT,
)
from data_analysis.reports import save_report
from data_analysis.table_profile import profile_table
from vertical_harness.plugins.contracts import (
    SideEffect,
    WorkflowContribution,
    WorkflowInputState,
    WorkflowMode,
    WorkflowOutputState,
)

logger = logging.getLogger(__name__)

# 工作流注册唯一标识符
DATA_ANALYSIS_WORKFLOW_NAME = "data_analysis_workflow"

# 匹配 Markdown 代码块中的 JSON 负载
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class DataAnalysisInputState(WorkflowInputState):
    """科研数据分析工作流输入状态模式（input_schema）。"""

    # 可选的数据文件路径（CSV、Parquet 或 XLSX）
    file_path: NotRequired[str]
    # 可选的 XLSX 工作表名称
    sheet_name: NotRequired[str | None]
    # 可选的列分页偏移量（从 0 开始）
    column_offset: NotRequired[int]
    # 可选的待校验中文 Markdown 报告正文
    markdown_report: NotRequired[str]
    # 可选的报告关联凭证文件相对路径列表
    evidence_files: NotRequired[list[str]]
    # 可选的任务类型：profile、validate_report 或 full_analysis
    task_type: NotRequired[str]


class DataAnalysisOutputState(WorkflowOutputState):
    """科研数据分析工作流输出状态模式（output_schema）。"""

    # 供调用方或 Supervisor 读取的结构化结果字典
    structured_response: NotRequired[dict[str, Any] | None]


class DataAnalysisInternalState(DataAnalysisInputState, DataAnalysisOutputState):
    """科研数据分析工作流内部流转状态模式（InternalState）。"""

    # 经入口校验后的受保护内部参数载荷
    validated_input: NotRequired[dict[str, Any] | None]
    # 入口解析或 Pydantic 校验失败时的错误原因
    validation_error: NotRequired[str | None]
    # 表格画像节点生成的画像结果字典
    profile_result: NotRequired[dict[str, Any] | None]
    # 报告校验与保存节点生成的发布结果字典
    report_result: NotRequired[dict[str, Any] | None]
    # 真实执行记录凭证列表
    analysis_executions: NotRequired[list[dict[str, Any]] | None]


# 规范标准别名导出
InternalState = DataAnalysisInternalState
input_schema = DataAnalysisInputState
output_schema = DataAnalysisOutputState


def _message_content_to_text(message: AnyMessage | dict[str, Any]) -> str:
    """提取 LangChain 消息对象或消息字典的纯文本内容。

    :param message: 消息对象或字典。
    :type message: langchain_core.messages.AnyMessage | dict[str, typing.Any]
    :return: 提取出的纯文本。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：获取消息 content 字段
    content = (
        message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    )
    # 第二步：规整为纯文本字符串
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    """从文本中提取 JSON 字典对象。

    :param text: 待提取的原始文本。
    :type text: str
    :return: 成功解析的字典，解析失败则返回 None。
    :rtype: dict[str, typing.Any] | None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：执行空串短路检查
    stripped = text.strip()
    if not stripped:
        return None

    # 第二步：搜集代码块 JSON 及大括号候选片段
    candidates: list[str] = [stripped]
    fenced_match = _FENCED_JSON_RE.search(stripped)
    if fenced_match is not None:
        candidates.insert(0, fenced_match.group(1).strip())

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        slice_part = stripped[first_brace : last_brace + 1]
        if slice_part not in candidates:
            candidates.append(slice_part)

    # 第三步：逐一尝试反序列化
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def parse_and_validate_input(state: DataAnalysisInternalState) -> dict[str, Any]:
    """工作流入口节点：解析并严格校验输入参数。

    :param state: 当前工作流状态快照。
    :type state: data_analysis.workflow.DataAnalysisInternalState
    :return: 更新 validated_input 与 validation_error 的增量字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info("parse_and_validate_input_started")
    raw_payload: dict[str, Any] = {}

    # 第一步：提取显式状态字段
    for field_name in (
        "file_path",
        "sheet_name",
        "column_offset",
        "markdown_report",
        "evidence_files",
        "task_type",
    ):
        val = state.get(field_name)  # type: ignore[arg-type]
        if val is not None:
            raw_payload[field_name] = val

    # 第二步：若缺少直接参数，回退检查最后一轮 messages 中的 JSON 负载
    if "file_path" not in raw_payload and "markdown_report" not in raw_payload:
        messages = state.get("messages") or []
        if messages:
            last_text = _message_content_to_text(messages[-1])
            parsed = _extract_json_dict(last_text)
            if parsed is not None:
                raw_payload = {**parsed, **raw_payload}

    # 第三步：核查是否具备至少一种分析任务载荷
    has_profile = "file_path" in raw_payload
    has_report = "markdown_report" in raw_payload

    if not has_profile and not has_report:
        error_msg = (
            "Missing data analysis parameters: provide 'file_path' for table profiling "
            "or 'markdown_report' and 'evidence_files' for report validation."
        )
        logger.warning("data_analysis_workflow_input_empty")
        return {
            "validated_input": None,
            "validation_error": error_msg,
            "profile_result": None,
            "report_result": None,
        }

    # 第四步：执行 Pydantic 强类型 schema 校验
    validated_dict: dict[str, Any] = dict(raw_payload)
    try:
        if has_profile:
            profile_model = TableProfileInput.model_validate(
                {
                    "file_path": str(raw_payload["file_path"]),
                    "sheet_name": raw_payload.get("sheet_name"),
                    "column_offset": raw_payload.get("column_offset", 0),
                }
            )
            validated_dict["file_path"] = profile_model.file_path
            validated_dict["sheet_name"] = profile_model.sheet_name
            validated_dict["column_offset"] = profile_model.column_offset

        if has_report:
            report_model = ReportValidationInput.model_validate(
                {
                    "markdown": str(raw_payload["markdown_report"]),
                    "evidence_files": list(raw_payload.get("evidence_files", [])),
                }
            )
            validated_dict["markdown_report"] = report_model.markdown
            validated_dict["evidence_files"] = list(report_model.evidence_files)

    except ValidationError as exc:
        logger.warning(
            "data_analysis_workflow_validation_failed",
            extra={"event_fields": {"error_count": len(exc.errors())}},
        )
        return {
            "validated_input": None,
            "validation_error": f"Invalid payload schema: {exc}",
            "profile_result": None,
            "report_result": None,
        }

    # 第五步：校验成功，构造受保护的 validated_input 字典返回
    logger.info("parse_and_validate_input_succeeded")
    return {
        "validated_input": validated_dict,
        "validation_error": None,
        "profile_result": None,
        "report_result": None,
    }


# 绑定节点系统提示词属性
parse_and_validate_input.prompt = INPUT_VALIDATION_NODE_PROMPT


def _route_after_validation(
    state: DataAnalysisInternalState,
) -> Literal["profile_table", "validate_report", "finalize_output"]:
    """校验后的确定性分支路由函数。

    :param state: 当前内部状态。
    :type state: data_analysis.workflow.DataAnalysisInternalState
    :return: 下一节点名称。
    :rtype: typing.Literal['profile_table', 'validate_report', 'finalize_output']
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：若有校验错误或缺少输入，直接跳转 finalize_output
    if state.get("validation_error") is not None:
        return "finalize_output"

    validated = state.get("validated_input")
    if not validated:
        return "finalize_output"
    task_type = validated.get("task_type")

    # 第二步：依据 task_type 或可用参数决策路由
    if task_type == "validate_report":
        return "validate_report"
    if "file_path" in validated:
        return "profile_table"
    if "markdown_report" in validated:
        return "validate_report"
    return "finalize_output"


def _route_after_profile(
    state: DataAnalysisInternalState,
) -> Literal["validate_report", "finalize_output"]:
    """表格画像节点完成后的后续路由。

    :param state: 当前内部状态。
    :type state: data_analysis.workflow.DataAnalysisInternalState
    :return: 下一节点名称。
    :rtype: typing.Literal['validate_report', 'finalize_output']
    :author: tongliga
    :date: 2026-10-19
    """
    validated = state.get("validated_input") or {}
    # 第一步：若同时提供了报告正文且未报错，流转至报告校验节点
    if "markdown_report" in validated and state.get("validation_error") is None:
        return "validate_report"
    # 第二步：否则直接流转至收尾输出节点
    return "finalize_output"


def _resolve_workspace_root(default_root: Path | None = None, config: Any = None) -> Path:
    """结合运行时配置与默认路径解析工作区物理绝对根路径。"""
    if config and isinstance(config, dict):
        cfg = config.get("configurable", {})
        if cfg.get("workspace_root"):
            return Path(cfg["workspace_root"]).resolve()
    if default_root is not None:
        return default_root.resolve()
    return Path("./.agent-workspace").resolve()


def create_profile_table_node(workspace_root: Path | None = None):
    """工厂函数：创建支持动态工作区解析的表格画像节点。"""

    def profile_table_node(
        state: DataAnalysisInternalState, config: RunnableConfig
    ) -> dict[str, Any]:
        """执行表格全量行扫描并生成列画像。"""
        logger.info("profile_table_node_started")
        validated = state.get("validated_input")
        if not validated or "file_path" not in validated:
            return {"validation_error": "Internal state missing 'file_path' for profile_table"}

        root = _resolve_workspace_root(workspace_root, config)
        file_arg = validated["file_path"]
        candidate = Path(str(file_arg).removeprefix("/workspace/").lstrip("/"))
        resolved_file = candidate if candidate.is_absolute() else (root / candidate)

        try:
            result = profile_table(
                file_path=resolved_file,
                run_dir=root,
                sheet_name=validated.get("sheet_name"),
                column_offset=validated.get("column_offset", 0),
            )
            logger.info("profile_table_node_succeeded")
            return {"profile_result": result}
        except Exception as exc:
            logger.error("profile_table_node_failed", exc_info=True)
            return {
                "validation_error": f"Table profile execution failed: {exc}",
                "profile_result": None,
            }

    profile_table_node.prompt = PROFILE_NODE_PROMPT
    return profile_table_node


def create_validate_report_node(workspace_root: Path | None = None):
    """工厂函数：创建支持动态工作区解析的报告校验节点。"""

    def validate_report_node(
        state: DataAnalysisInternalState, config: RunnableConfig
    ) -> dict[str, Any]:
        """执行科研报告校验与保存发布。"""
        logger.info("validate_report_node_started")
        validated = state.get("validated_input")
        if not validated or "markdown_report" not in validated:
            return {"validation_error": "Internal state missing 'markdown_report'"}

        root = _resolve_workspace_root(workspace_root, config)
        markdown = validated["markdown_report"]
        evidence_files = validated.get("evidence_files", [])
        executions = state.get("analysis_executions") or []

        try:
            saved_path = save_report(
                markdown=markdown,
                evidence_files=evidence_files,
                run_dir=root,
                executions=executions,
            )
            logger.info(
                "validate_report_node_succeeded", extra={"event_fields": {"path": str(saved_path)}}
            )
            return {
                "report_result": {
                    "status": "passed",
                    "report_path": str(saved_path),
                }
            }
        except Exception as exc:
            logger.warning(
                "validate_report_node_rejected", extra={"event_fields": {"reason": str(exc)}}
            )
            return {
                "report_result": {
                    "status": "failed",
                    "error": str(exc),
                }
            }

    validate_report_node.prompt = VALIDATE_REPORT_NODE_PROMPT
    return validate_report_node


def finalize_output(state: DataAnalysisInternalState) -> dict[str, Any]:
    """工作流出口节点：组装符合 output_schema 契约的标准响应。

    :param state: 当前内部状态。
    :type state: data_analysis.workflow.DataAnalysisInternalState
    :return: 包含 messages 与 structured_response 的输出状态字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info("finalize_output_started")
    validation_error = state.get("validation_error")
    profile_result = state.get("profile_result")
    report_result = state.get("report_result")

    # 第一步：若存在校验错误，构造错误 payload
    if validation_error is not None:
        error_payload = {
            "status": "invalid_input",
            "error": validation_error,
        }
        return {
            "messages": [AIMessage(content=json.dumps(error_payload, ensure_ascii=False))],
            "structured_response": error_payload,
        }

    # 第二步：成功完成时组装综合响应结构
    payload: dict[str, Any] = {"status": "passed"}
    if profile_result is not None:
        payload["profile"] = profile_result
    if report_result is not None:
        payload["report"] = report_result

    logger.info("finalize_output_succeeded", extra={"event_fields": {"status": payload["status"]}})
    return {
        "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))],
        "structured_response": payload,
    }


# 绑定节点系统提示词属性
finalize_output.prompt = FINALIZE_OUTPUT_NODE_PROMPT


def build_data_analysis_workflow(
    *,
    workspace_root: Path | None = None,
    checkpointer: BaseCheckpointSaver[Any] | bool | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """构建并编译基于 LangGraph 的科研数据分析工作流。

    :param workspace_root: 工作区绝对物理路径，默认当前工作目录下的 .agent-workspace。
    :type workspace_root: pathlib.Path | None
    :param checkpointer: 可选的检查点保存器或父图继承布尔标记。
    :type checkpointer: langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any] | bool | None
    :return: 预编译的 CompiledStateGraph 实例。
    :rtype: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    root = (workspace_root or Path("./.agent-workspace")).resolve()
    logger.info(
        "build_data_analysis_workflow_started",
        extra={"event_fields": {"workspace_root": str(root)}},
    )

    # 第一步：创建严格声明三层状态契约的 StateGraph 构造器
    builder: StateGraph[Any, Any, Any, Any] = StateGraph(
        DataAnalysisInternalState,
        input_schema=DataAnalysisInputState,
        output_schema=DataAnalysisOutputState,
    )

    # 第二步：注册处理节点
    builder.add_node("parse_and_validate_input", parse_and_validate_input)
    builder.add_node("profile_table", create_profile_table_node(root))
    builder.add_node("validate_report", create_validate_report_node(root))
    builder.add_node("finalize_output", finalize_output)

    # 第三步：配置拓扑连线与分支条件路由
    builder.add_edge(START, "parse_and_validate_input")
    builder.add_conditional_edges(
        "parse_and_validate_input",
        _route_after_validation,
        {
            "profile_table": "profile_table",
            "validate_report": "validate_report",
            "finalize_output": "finalize_output",
        },
    )
    builder.add_conditional_edges(
        "profile_table",
        _route_after_profile,
        {
            "validate_report": "validate_report",
            "finalize_output": "finalize_output",
        },
    )
    builder.add_edge("validate_report", "finalize_output")
    builder.add_edge("finalize_output", END)

    # 第四步：编译并命名工作流图实例返回
    compiled = builder.compile(checkpointer=checkpointer, name=DATA_ANALYSIS_WORKFLOW_NAME)
    logger.info("build_data_analysis_workflow_compiled")
    return compiled


# 模块级预编译 LangGraph 实例
graph = build_data_analysis_workflow()


def build_data_analysis_workflow_contribution(
    *,
    workspace_root: Path | None = None,
    mode: WorkflowMode | None = None,
) -> WorkflowContribution:
    """构造 data_analysis_workflow 工作流插件贡献声明。

    :param workspace_root: 可选的工作区物理路径。
    :type workspace_root: pathlib.Path | None
    :param mode: 可选强制指定的工作流接入模式（compiled 或 async）。
    :type mode: vertical_harness.plugins.contracts.WorkflowMode | None
    :return: 经过三层状态模式校验的 WorkflowContribution 实例。
    :rtype: vertical_harness.plugins.contracts.WorkflowContribution
    :author: tongliga
    :date: 2026-10-19
    """
    compiled_runnable = (
        build_data_analysis_workflow(workspace_root=workspace_root)
        if workspace_root is not None
        else graph
    )
    return WorkflowContribution(
        name=DATA_ANALYSIS_WORKFLOW_NAME,
        description=(
            "Execute deterministic tabular data profiling and research report validation workflow. "
            "Provide 'file_path' for profiling or 'markdown_report' and 'evidence_files' for report validation."
        ),
        internal_state=DataAnalysisInternalState,
        input_schema=DataAnalysisInputState,
        output_schema=DataAnalysisOutputState,
        runnable=compiled_runnable,
        graph_id=DATA_ANALYSIS_WORKFLOW_NAME,
        mode=mode,
        context_mode="isolated",
        durability="sync",
        effect=SideEffect.READ,
        idempotent=True,
    )


__all__ = [
    "DATA_ANALYSIS_WORKFLOW_NAME",
    "DataAnalysisInputState",
    "DataAnalysisInternalState",
    "DataAnalysisOutputState",
    "InternalState",
    "build_data_analysis_workflow",
    "build_data_analysis_workflow_contribution",
    "graph",
    "input_schema",
    "output_schema",
    "parse_and_validate_input",
]
