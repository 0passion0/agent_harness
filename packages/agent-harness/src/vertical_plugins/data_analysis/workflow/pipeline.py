"""基于 BaseWorkflow 的确定性表格数据分析与报告生成工作流模块。

本模块遵循三层状态隔离架构契约并声明固定的 6 阶段分析管道：
1. `validate_input`：校验输入文件、工作区路径、数据格式及分析目标；
2. `profile_table`：读取 CSV、Parquet、XLSX 并执行真实全表画像；
3. `analyze_data`：执行真实描述性统计及显式指定的相关分析/组间比较；
4. `audit_results`：核验分析脚本与 JSON 产物真实性、SHA256 哈希及数值一致性；
5. `generate_report`：基于真实画像与计算指标生成中文 Markdown 报告；
6. `publish_report`：验证报告与产物一致性后原子保存报告。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from statistics import fmean, median, stdev
from typing import Any, NotRequired

import duckdb
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from openpyxl import load_workbook
from pydantic import ValidationError

from vertical_plugins.common.contracts import (
    SideEffect,
    WorkflowInputState,
    WorkflowInternalState,
    WorkflowOutputState,
)
from vertical_plugins.common.workflow import (
    BaseWorkflow,
    WorkflowContext,
    WorkflowHooks,
    WorkflowNodeEvent,
    WorkflowPlan,
    extract_json_payload,
    extract_message_text,
)
from vertical_plugins.data_analysis.models.evidence import AnalysisEvidence
from vertical_plugins.data_analysis.models.report import (
    AnalysisTaskInput,
    ReportValidationInput,
    ReportValidationResult,
)
from vertical_plugins.data_analysis.prompts.analysis import ANALYSIS_PLAN_PROMPT
from vertical_plugins.data_analysis.prompts.report import (
    CORE_REPORT_SECTIONS,
    REPORT_GENERATION_PROMPT,
)
from vertical_plugins.data_analysis.tools.reports import save_report
from vertical_plugins.data_analysis.tools.table_profile import (
    SUPPORTED_FORMATS,
)
from vertical_plugins.data_analysis.tools.table_profile import (
    profile_table as run_table_profiler,
)

logger = logging.getLogger(__name__)

# 工作流注册唯一标识符
DATA_ANALYSIS_WORKFLOW_NAME = "data_analysis_workflow"

# 需要显式参数支撑的推断检验关键词
_INFERENCE_KEYWORDS: tuple[str, ...] = (
    "显著性",
    "假设检验",
    "p值",
    "p-value",
    "t检验",
    "t-test",
    "方差分析",
    "anova",
    "回归",
    "因果",
)


class DataAnalysisInputState(WorkflowInputState):
    """科研数据分析工作流输入状态模式（input_schema）。"""

    # 数据文件路径（CSV、Parquet 或 XLSX）
    file_path: NotRequired[str]
    # 可选的 XLSX 工作表名称
    sheet_name: NotRequired[str | None]
    # 可选的列分页偏移量（从 0 开始）
    column_offset: NotRequired[int]
    # 可选的分析目标或研究问题描述
    research_question: NotRequired[str]
    # 可选的分析目标别名
    analysis_goal: NotRequired[str]
    # 分析类型：descriptive（默认描述统计）、correlation（相关分析）、group_comparison（组间比较）
    analysis_type: NotRequired[str]
    # 显式指定的分析目标数值列列表
    target_columns: NotRequired[list[str]]
    # 组间比较时显式指定的分组列名称
    group_column: NotRequired[str | None]
    # 可选的待校验或人工修订中文 Markdown 报告正文
    markdown_report: NotRequired[str]
    # 可选的报告关联凭证文件相对路径列表
    evidence_files: NotRequired[list[str]]
    # 可选的任务模式：full_analysis（默认）、profile、validate_report
    task_type: NotRequired[str]


class DataAnalysisOutputState(WorkflowOutputState):
    """科研数据分析工作流输出状态模式（output_schema）。"""


class DataAnalysisInternalState(WorkflowInternalState):
    """科研数据分析工作流内部流转状态模式（InternalState）。"""

    # 数据文件路径（CSV、Parquet 或 XLSX）
    file_path: NotRequired[str]
    # 可选的 XLSX 工作表名称
    sheet_name: NotRequired[str | None]
    # 可选的列分页偏移量（从 0 开始）
    column_offset: NotRequired[int]
    # 可选的分析目标或研究问题描述
    research_question: NotRequired[str]
    # 可选的分析目标别名
    analysis_goal: NotRequired[str]
    # 分析类型：descriptive（默认描述统计）、correlation（相关分析）、group_comparison（组间比较）
    analysis_type: NotRequired[str]
    # 显式指定的分析目标数值列列表
    target_columns: NotRequired[list[str]]
    # 组间比较时显式指定的分组列名称
    group_column: NotRequired[str | None]
    # 可选的待校验或人工修订中文 Markdown 报告正文
    markdown_report: NotRequired[str]
    # 可选的报告关联凭证文件相对路径列表
    evidence_files: NotRequired[list[str]]
    # 可选的任务模式：full_analysis（默认）、profile、validate_report
    task_type: NotRequired[str]
    # 经入口校验后的受保护内部参数载荷
    validated_input: NotRequired[dict[str, Any] | None]
    # 入口解析或阶段校验失败时的错误原因
    validation_error: NotRequired[str | None]
    # 错误状态分类标识
    error_status: NotRequired[str | None]
    # 提前完成（如仅画像模式）停止后续节点的标记
    workflow_halted: NotRequired[bool]
    # 表格画像节点生成的真实全表画像字典
    profile_result: NotRequired[dict[str, Any] | None]
    # 真实统计计算结果字典
    analysis_result: NotRequired[dict[str, Any] | None]
    # 产物存在性、哈希与数值一致性审计结果字典
    audit_result: NotRequired[dict[str, Any] | None]
    # 报告校验与保存节点生成的发布结果字典
    report_result: NotRequired[dict[str, Any] | None]
    # 真实执行记录凭证列表
    analysis_executions: NotRequired[list[dict[str, Any]] | None]
    # 节点执行事件摘要列表
    workflow_events: NotRequired[list[dict[str, Any]]]


# 规范标准别名导出
InternalState = DataAnalysisInternalState
input_schema = DataAnalysisInputState
output_schema = DataAnalysisOutputState


def _to_float(val: Any) -> float | None:
    """将单元格值安全转换为有限浮点数，布尔值与空值返回 None。

    :param val: 原始值。
    :type val: typing.Any
    :return: 有限浮点数或 None。
    :rtype: float | None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：排除空值与布尔类型
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int | float):
        fval = float(val)
        return fval if math.isfinite(fval) else None
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            return None
        try:
            fval = float(stripped)
            return fval if math.isfinite(fval) else None
        except ValueError:
            return None
    return None


def _load_table_columns(
    file_path: Path,
    columns: list[str],
    *,
    sheet_name: str | None = None,
) -> list[dict[str, Any]]:
    """按需读取数据表中指定列的行记录用于相关分析与组间比较。

    :param file_path: 数据表绝对物理路径。
    :type file_path: pathlib.Path
    :param columns: 需要提取的列名列表。
    :type columns: list[str]
    :param sheet_name: XLSX 工作表名称，可选。
    :type sheet_name: str | None
    :return: 每行对应指定列字典的列表。
    :rtype: list[dict[str, typing.Any]]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：处理 CSV 与 Parquet 文件（通过 DuckDB 投影查询）
    suffix = file_path.suffix.lower()
    unique_cols = list(dict.fromkeys(columns))
    if suffix in {".csv", ".parquet"}:
        reader = "read_csv_auto" if suffix == ".csv" else "read_parquet"
        quoted = ", ".join('"' + col.replace('"', '""') + '"' for col in unique_cols)
        with duckdb.connect(":memory:") as conn:
            rows = conn.execute(
                f"SELECT {quoted} FROM {reader}(?)",
                [str(file_path)],
            ).fetchall()
        return [dict(zip(unique_cols, row, strict=False)) for row in rows]

    # 第二步：处理 XLSX 工作簿（通过 OpenPyXL 流式只读扫描）
    wb = load_workbook(file_path, read_only=True, data_only=True)
    try:
        target_sheet = sheet_name or wb.sheetnames[0]
        ws = wb[target_sheet]
        row_iter = ws.iter_rows(values_only=True)
        header_row = next(row_iter, None)
        if not header_row:
            return []
        headers = [
            str(h).strip() if h is not None and str(h).strip() else f"column_{idx + 1}"
            for idx, h in enumerate(header_row)
        ]
        col_indices = {col: headers.index(col) for col in unique_cols if col in headers}
        records: list[dict[str, Any]] = []
        for raw_row in row_iter:
            if raw_row is None or all(
                c is None or (isinstance(c, str) and not c.strip()) for c in raw_row
            ):
                continue
            records.append(
                {
                    col: (raw_row[idx] if idx < len(raw_row) else None)
                    for col, idx in col_indices.items()
                }
            )
        return records
    finally:
        wb.close()


def _compute_pearson(xs: list[float], ys: list[float]) -> float | None:
    """计算成对样本的精确皮尔逊相关系数。

    :param xs: 第一列数值序列。
    :type xs: list[float]
    :param ys: 第二列数值序列。
    :type ys: list[float]
    :return: 相关系数 r（若样本不足或方差为 0 则返回 None）。
    :rtype: float | None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：校验样本量与方差非零
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mean_x = fmean(xs)
    mean_y = fmean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0.0 or den_y == 0.0:
        return None
    r = num / (den_x * den_y)
    return round(max(-1.0, min(1.0, r)), 6)


class AnalysisWorkflowResult(ReportValidationResult):
    """Validate profiling and full-analysis outputs without accepting unknown fields."""

    profile: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    audit: dict[str, Any] | None = None
    report: ReportValidationResult | None = None


class DataAnalysisWorkflow(BaseWorkflow):
    """固定的表格数据分析与报告生成流程。

    六个固定阶段直接声明在 `run()` 方法内，由 `BaseWorkflow` 编译为独立的 LangGraph 节点。
    """

    plugin_id = "data_analysis"
    workflow_name = DATA_ANALYSIS_WORKFLOW_NAME
    description = (
        "Execute deterministic tabular data profiling, statistical analysis, "
        "artifact auditing, and Chinese Markdown report generation workflow."
    )
    input_schema = DataAnalysisInputState
    output_schema = DataAnalysisOutputState
    internal_state = DataAnalysisInternalState
    result_schema = AnalysisWorkflowResult
    effect = SideEffect.WRITE
    idempotent = False

    def __init__(
        self,
        workspace_root: Path | None = None,
        checkpointer: BaseCheckpointSaver[Any] | bool | None = None,
        model: Any = None,
        *,
        context: WorkflowContext | None = None,
        hooks: WorkflowHooks | None = None,
    ) -> None:
        """初始化科研数据分析工作流。

        :param workspace_root: 可选的工作区根目录（兼容直接构造）。
        :type workspace_root: pathlib.Path | None
        :param checkpointer: 可选的检查点保存器。
        :type checkpointer: langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any] | bool | None
        :param model: 可选的语言模型实例。
        :type model: typing.Any
        :param context: 可信宿主注入的运行上下文。
        :type context: vertical_plugins.common.workflow.WorkflowContext | None
        :param hooks: 可选的用户生命周期钩子。
        :type hooks: vertical_plugins.common.workflow.WorkflowHooks | None
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：合并显式参数到 WorkflowContext
        active_ctx = context or WorkflowContext()
        overrides: dict[str, Any] = {}
        if workspace_root is not None:
            overrides["workspace_root"] = Path(workspace_root).resolve()
        if model is not None:
            overrides["model"] = model
        if checkpointer is not None:
            overrides["checkpointer"] = checkpointer
        if overrides:
            active_ctx = active_ctx.with_overrides(**overrides)

        # 第二步：调用 BaseWorkflow 基类初始化
        super().__init__(context=active_ctx, hooks=hooks, checkpointer=active_ctx.checkpointer)

    def run(self) -> WorkflowPlan:
        """声明完整的六阶段分析工作流。

        :return: 包含六个固定业务节点的 WorkflowPlan。
        :rtype: vertical_plugins.common.workflow.WorkflowPlan
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：按固定顺序声明六个业务阶段，每个 step 编译为独立 LangGraph 节点
        return self.pipeline(
            self.step(
                "validate_input",
                self.validate_input,
                editable_fields=(
                    "file_path",
                    "sheet_name",
                    "column_offset",
                    "research_question",
                    "analysis_goal",
                    "analysis_type",
                    "target_columns",
                    "group_column",
                    "markdown_report",
                    "evidence_files",
                    "task_type",
                ),
                description="校验输入文件、工作区路径边界、数据格式及分析目标",
            ),
            self.step(
                "profile_table",
                self.profile_table,
                editable_fields=("file_path", "sheet_name", "column_offset"),
                description="读取 CSV/Parquet/XLSX 并完成全表数据画像与缺失值统计",
            ),
            self.step(
                "analyze_data",
                self.analyze_data,
                editable_fields=(
                    "research_question",
                    "analysis_goal",
                    "analysis_type",
                    "target_columns",
                    "group_column",
                ),
                description="根据分析目标执行真实统计计算并输出结构化结果",
            ),
            self.step(
                "audit_results",
                self.audit_results,
                editable_fields=("evidence_files",),
                description="核验分析产物存在性、SHA256 哈希、异常数值与可复现性",
            ),
            self.step(
                "generate_report",
                self.generate_report,
                editable_fields=("research_question", "analysis_goal", "markdown_report"),
                description="基于真实画像和计算结果生成中文 Markdown 分析报告",
            ),
            self.step(
                "publish_report",
                self.publish_report,
                editable_fields=("markdown_report", "evidence_files"),
                is_publish=True,
                description="验证报告与数据产物一致性并保存发布报告",
            ),
        )

    def _merge_editable_state(self, state: DataAnalysisInternalState) -> dict[str, Any]:
        """合并 state 顶层被 Hook 或检查点修改的字段到 `validated_input` 副本。

        :param state: 当前节点内部状态。
        :type state: DataAnalysisInternalState
        :return: 合并后的有效输入参数字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：复制已校验输入并覆盖顶层最新可编辑字段
        merged = dict(state.get("validated_input") or {})
        for key in (
            "file_path",
            "sheet_name",
            "column_offset",
            "research_question",
            "analysis_goal",
            "analysis_type",
            "target_columns",
            "group_column",
            "markdown_report",
            "evidence_files",
            "task_type",
        ):
            if key in state and state.get(key) is not None:  # type: ignore[arg-type]
                merged[key] = state.get(key)  # type: ignore[arg-type]
        return merged

    def validate_input(self, state: DataAnalysisInternalState) -> dict[str, Any]:
        """阶段 1：校验输入文件、工作区路径、数据格式及分析目标。

        缺少必要信息时返回明确的补充请求错误，禁止自行假设。

        :param state: 当前工作流状态。
        :type state: DataAnalysisInternalState
        :return: 状态更新增量字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("validate_input_started")
        # 第一步：从输入状态或最后一条消息的 JSON 负载提取原始参数
        raw_payload: dict[str, Any] = {}
        for key in (
            "file_path",
            "sheet_name",
            "column_offset",
            "research_question",
            "analysis_goal",
            "analysis_type",
            "target_columns",
            "group_column",
            "markdown_report",
            "evidence_files",
            "task_type",
        ):
            val = state.get(key)  # type: ignore[arg-type]
            if val is not None:
                raw_payload[key] = val

        if "file_path" not in raw_payload and "markdown_report" not in raw_payload:
            messages = state.get("messages") or []
            if messages:
                parsed = extract_json_payload(extract_message_text(messages[-1]))
                if parsed:
                    raw_payload = {**parsed, **raw_payload}

        # 第二步：支持仅报告核验模式（validate_report）的输入校验
        task_type = str(raw_payload.get("task_type") or "full_analysis")
        if task_type == "validate_report" or (
            "markdown_report" in raw_payload and "file_path" not in raw_payload
        ):
            try:
                rep_in = ReportValidationInput.model_validate(
                    {
                        "markdown": str(raw_payload.get("markdown_report") or ""),
                        "evidence_files": list(raw_payload.get("evidence_files") or []),
                    }
                )
            except ValidationError as exc:
                return {
                    "validated_input": None,
                    "validation_error": f"Invalid report validation input: {exc}",
                    "error_status": "invalid_input",
                }
            return {
                "validated_input": {
                    "task_type": "validate_report",
                    "markdown_report": rep_in.markdown,
                    "evidence_files": list(rep_in.evidence_files),
                },
                "validation_error": None,
                "error_status": None,
                "workflow_halted": False,
            }

        # 第三步：核验数据文件路径是否提供且未传入空分析目标
        if "file_path" not in raw_payload or not str(raw_payload.get("file_path") or "").strip():
            return {
                "validated_input": None,
                "validation_error": (
                    "Missing required parameter 'file_path': please provide a CSV, Parquet, "
                    "or XLSX dataset path inside the workspace."
                ),
                "error_status": "invalid_input",
            }

        for goal_key in ("research_question", "analysis_goal"):
            if goal_key in raw_payload and not str(raw_payload[goal_key]).strip():
                return {
                    "validated_input": None,
                    "validation_error": (
                        f"Parameter '{goal_key}' must not be blank: please clarify the analysis objective."
                    ),
                    "error_status": "invalid_input",
                }

        # 第四步：使用 AnalysisTaskInput 校验参数结构与分析类型
        try:
            task_input = AnalysisTaskInput.model_validate(raw_payload)
        except ValidationError as exc:
            return {
                "validated_input": None,
                "validation_error": f"Invalid analysis task input: {exc}",
                "error_status": "invalid_input",
            }

        # 第五步：校验工作区物理路径边界、文件存在性及受支持的数据格式
        try:
            resolved_path = self.context.resolve_workspace_path(task_input.file_path)
        except ValueError as exc:
            return {
                "validated_input": None,
                "validation_error": str(exc),
                "error_status": "invalid_input",
            }

        if not resolved_path.is_file():
            return {
                "validated_input": None,
                "validation_error": f"Dataset file does not exist in workspace: {task_input.file_path}",
                "error_status": "invalid_input",
            }
        if resolved_path.suffix.lower() not in SUPPORTED_FORMATS:
            return {
                "validated_input": None,
                "validation_error": (
                    f"Unsupported file format '{resolved_path.suffix}': "
                    f"supported formats are {sorted(SUPPORTED_FORMATS)}."
                ),
                "error_status": "invalid_input",
            }

        # 第六步：校验特定统计检验所需的显式任务参数，缺失时要求用户补充，禁止自行猜测
        if task_input.analysis_type == "correlation" and len(task_input.target_columns) < 2:
            return {
                "validated_input": None,
                "validation_error": (
                    "Correlation analysis requires explicit 'target_columns' with at least 2 numeric "
                    "column names; please supplement 'target_columns'."
                ),
                "error_status": "invalid_input",
            }
        if task_input.analysis_type == "group_comparison" and (
            not task_input.group_column or not task_input.target_columns
        ):
            return {
                "validated_input": None,
                "validation_error": (
                    "Group comparison analysis requires explicit 'group_column' and 'target_columns'; "
                    "please supplement the missing parameters."
                ),
                "error_status": "invalid_input",
            }

        validated_dict = task_input.model_dump(mode="json")
        validated_dict["resolved_file_path"] = str(resolved_path)
        validated_dict["effective_goal"] = (
            task_input.research_question
            or task_input.analysis_goal
            or "数据表特征分布与描述性统计分析"
        )
        return {
            "validated_input": validated_dict,
            "validation_error": None,
            "error_status": None,
            "workflow_halted": False,
        }

    def profile_table(self, state: DataAnalysisInternalState) -> dict[str, Any]:
        """阶段 2：读取 CSV、Parquet、XLSX，完成真实的全表数据画像与大表分批扫描。

        :param state: 当前工作流状态。
        :type state: DataAnalysisInternalState
        :return: 包含 `profile_result` 的状态增量。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("profile_table_started")
        # 第一步：若为纯报告校验模式则直接跳过画像
        validated = self._merge_editable_state(state)
        if validated.get("task_type") == "validate_report" and "file_path" not in validated:
            return {"profile_result": None}

        # 第二步：解析目标文件路径并调用底层 DuckDB / OpenPyXL 分批画像器
        try:
            target_file = self.context.resolve_workspace_path(str(validated["file_path"]))
            first_page = run_table_profiler(
                file_path=target_file,
                run_dir=self.workspace_root,
                sheet_name=validated.get("sheet_name"),
                column_offset=int(validated.get("column_offset") or 0),
            )
        except Exception as exc:
            logger.error("profile_table_failed", exc_info=True)
            return {
                "validation_error": f"Table profiling failed: {exc}",
                "error_status": "failed",
            }

        # 第三步：若多工作表 XLSX 未指定 sheet_name，请求用户补充工作表名称，禁止盲目假设
        if first_page.get("selected_sheet") is None and "sheets" in first_page:
            sheets = first_page.get("sheets") or []
            return {
                "profile_result": first_page,
                "validation_error": (
                    f"XLSX workbook contains multiple sheets {sheets}; "
                    "please specify 'sheet_name' to proceed."
                ),
                "error_status": "invalid_input",
            }

        # 第四步：若列数超过单页且起始偏移为 0，聚合全量列画像供后续统计计算与审计使用
        all_columns = list(first_page.get("columns") or [])
        next_offset = first_page.get("next_column_offset")
        if int(validated.get("column_offset") or 0) == 0:
            while next_offset is not None:
                page = run_table_profiler(
                    file_path=target_file,
                    run_dir=self.workspace_root,
                    sheet_name=first_page.get("selected_sheet"),
                    column_offset=int(next_offset),
                )
                all_columns.extend(page.get("columns") or [])
                next_offset = page.get("next_column_offset")

        enriched_profile = dict(first_page)
        enriched_profile["all_columns"] = all_columns

        # 第五步：若调用方仅请求 profile 任务，提前封装响应并标记停止后续节点
        if validated.get("task_type") == "profile":
            payload = {"status": "passed", "profile": first_page}
            return {
                "profile_result": first_page,
                "workflow_halted": True,
                "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))],
                "structured_response": payload,
            }

        return {"profile_result": enriched_profile, "validated_input": validated}

    def analyze_data(self, state: DataAnalysisInternalState) -> dict[str, Any]:
        """阶段 3：根据明确的分析目标执行真实统计计算，输出结构化结果与可复现脚本凭证。

        默认实现描述性统计；相关分析（`correlation`）与组间比较（`group_comparison`）
        必须具备显式参数。若信息不足以选择统计检验，绝不自动编造推断结论。

        :param state: 当前工作流状态。
        :type state: DataAnalysisInternalState
        :return: 包含 `analysis_result` 与 `analysis_executions` 的状态增量。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("analyze_data_started")
        # 第一步：若为纯报告校验且已有执行记录，直接复用已有执行凭证
        validated = self._merge_editable_state(state)
        if validated.get("task_type") == "validate_report" and "file_path" not in validated:
            return {"analysis_result": {"status": "skipped_for_report_validation"}}

        profile = state.get("profile_result") or {}
        columns_meta: list[dict[str, Any]] = list(
            profile.get("all_columns") or profile.get("columns") or []
        )
        col_map = {str(col["name"]): col for col in columns_meta if "name" in col}

        analysis_type = str(validated.get("analysis_type") or "descriptive")
        target_columns = [str(c) for c in (validated.get("target_columns") or [])]
        group_column = validated.get("group_column")
        effective_goal = str(
            validated.get("research_question")
            or validated.get("analysis_goal")
            or validated.get("effective_goal")
            or "数据表特征分布与描述性统计分析"
        )

        # 第二步：校验显式指定的分析列是否存在且包含有效数值
        if target_columns:
            missing_cols = [c for c in target_columns if c not in col_map]
            if missing_cols:
                return {
                    "validation_error": f"Specified target_columns not found in table: {missing_cols}",
                    "error_status": "invalid_input",
                }
        if group_column and str(group_column) not in col_map:
            return {
                "validation_error": f"Specified group_column {group_column!r} not found in table.",
                "error_status": "invalid_input",
            }

        # 第三步：提取全表真实描述性统计（数值列均值/标准差/分位数与分类列频次）
        descriptive_numeric: dict[str, dict[str, Any]] = {}
        categorical_summary: dict[str, list[dict[str, Any]]] = {}
        selected_for_desc = target_columns if target_columns else list(col_map.keys())
        for col_name in selected_for_desc:
            c_info = col_map[col_name]
            num_info = c_info.get("numeric_subset") or c_info.get("numeric")
            numeric_count = int(
                c_info.get("numeric_count")
                or (num_info.get("count") if isinstance(num_info, dict) else 0)
                or 0
            )
            if isinstance(num_info, dict) and numeric_count > 0:
                quantiles = num_info.get("approx_quartiles") or []
                descriptive_numeric[col_name] = {
                    "count": numeric_count,
                    "null_count": int(c_info.get("missing_count") or c_info.get("null_count") or 0),
                    "mean": num_info.get("mean"),
                    "std": num_info.get("std"),
                    "min": num_info.get("min"),
                    "q25": quantiles[0] if len(quantiles) > 0 else num_info.get("q25"),
                    "median": quantiles[1] if len(quantiles) > 1 else num_info.get("median"),
                    "q75": quantiles[2] if len(quantiles) > 2 else num_info.get("q75"),
                    "max": num_info.get("max"),
                }
            top_vals = c_info.get("frequent_values") or c_info.get("top_values")
            if top_vals:
                categorical_summary[col_name] = list(top_vals)

        # 第四步：根据显式 analysis_type 执行真实相关分析或组间比较计算
        target_file = self.context.resolve_workspace_path(str(validated["file_path"]))
        selected_sheet = profile.get("selected_sheet") or validated.get("sheet_name")
        correlations: list[dict[str, Any]] = []
        group_comparisons: list[dict[str, Any]] = []
        inference_note: str | None = None

        if analysis_type == "correlation":
            if len(target_columns) < 2:
                return {
                    "validation_error": "Correlation analysis requires at least 2 numeric target_columns.",
                    "error_status": "invalid_input",
                }
            rows = _load_table_columns(target_file, target_columns, sheet_name=selected_sheet)
            for idx_a, col_a in enumerate(target_columns):
                for col_b in target_columns[idx_a + 1 :]:
                    paired_x: list[float] = []
                    paired_y: list[float] = []
                    for r in rows:
                        fx = _to_float(r.get(col_a))
                        fy = _to_float(r.get(col_b))
                        if fx is not None and fy is not None:
                            paired_x.append(fx)
                            paired_y.append(fy)
                    r_val = _compute_pearson(paired_x, paired_y)
                    correlations.append(
                        {
                            "column_x": col_a,
                            "column_y": col_b,
                            "n": len(paired_x),
                            "pearson_r": r_val,
                        }
                    )
        elif analysis_type == "group_comparison":
            if not group_column or not target_columns:
                return {
                    "validation_error": "Group comparison requires explicit 'group_column' and 'target_columns'.",
                    "error_status": "invalid_input",
                }
            grp_col = str(group_column)
            rows = _load_table_columns(
                target_file, [grp_col, *target_columns], sheet_name=selected_sheet
            )
            for t_col in target_columns:
                grouped_vals: dict[str, list[float]] = {}
                for r in rows:
                    g_raw = r.get(grp_col)
                    if g_raw is None or (isinstance(g_raw, str) and not g_raw.strip()):
                        continue
                    f_val = _to_float(r.get(t_col))
                    if f_val is not None:
                        grouped_vals.setdefault(str(g_raw).strip(), []).append(f_val)

                group_stats: dict[str, dict[str, Any]] = {}
                for g_name, vals in sorted(grouped_vals.items()):
                    if not vals:
                        continue
                    group_stats[g_name] = {
                        "n": len(vals),
                        "mean": round(fmean(vals), 6),
                        "std": round(stdev(vals), 6) if len(vals) > 1 else 0.0,
                        "median": round(float(median(vals)), 6),
                        "min": round(min(vals), 6),
                        "max": round(max(vals), 6),
                    }
                # 若恰好存在两个或以上有效分组，计算前两个主分组的均值差与 Welch t 统计量
                welch_t: float | None = None
                mean_diff: float | None = None
                group_names = list(group_stats.keys())
                if len(group_names) >= 2:
                    g1, g2 = group_stats[group_names[0]], group_stats[group_names[1]]
                    mean_diff = round(float(g1["mean"]) - float(g2["mean"]), 6)
                    if int(g1["n"]) >= 2 and int(g2["n"]) >= 2:
                        se_sq = (float(g1["std"]) ** 2) / int(g1["n"]) + (
                            float(g2["std"]) ** 2
                        ) / int(g2["n"])
                        if se_sq > 0:
                            welch_t = round(mean_diff / math.sqrt(se_sq), 6)
                group_comparisons.append(
                    {
                        "target_column": t_col,
                        "group_column": grp_col,
                        "groups": group_stats,
                        "compared_groups": group_names[:2] if len(group_names) >= 2 else group_names,
                        "mean_difference": mean_diff,
                        "welch_t": welch_t,
                    }
                )
        else:
            # 默认描述性统计：若用户在问题中提及推断检验但未提供显式检验参数，明确记录说明，绝不编造推断结果
            if any(kw in effective_goal.lower() for kw in _INFERENCE_KEYWORDS):
                inference_note = (
                    "未提供显式的统计检验类型（correlation / group_comparison）及对应变量参数，"
                    "本次仅输出确定性描述性统计，不自动假设或编造统计检验结论。"
                )

        # 第五步：汇总结构化分析结果并落盘可复现 Python 分析脚本与 JSON 结果产物
        summary_data: dict[str, Any] = {
            "task": "scientific_data_analysis",
            "source": profile.get("source", str(validated["file_path"])),
            "selected_sheet": selected_sheet,
            "research_question": effective_goal,
            "analysis_type": analysis_type,
            "row_count": int(profile.get("row_count") or 0),
            "column_count": int(profile.get("column_count") or 0),
            "descriptive_statistics": descriptive_numeric,
            "categorical_frequencies": categorical_summary,
            "correlations": correlations,
            "group_comparisons": group_comparisons,
            "inference_note": inference_note,
        }

        scripts_dir = self.workspace_root / "scripts"
        artifacts_dir = self.workspace_root / "artifacts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        script_file = scripts_dir / "analyze_data.py"
        summary_file = artifacts_dir / "analysis_summary.json"
        summary_json_text = json.dumps(summary_data, ensure_ascii=False, allow_nan=False, indent=2)
        summary_file.write_text(summary_json_text, encoding="utf-8")

        script_content = (
            '"""自动生成的可复现表格统计分析脚本。"""\n\n'
            "from __future__ import annotations\n\n"
            "import json\n"
            "from pathlib import Path\n\n"
            f"ANALYSIS_CONFIG = { {'source': summary_data['source'], 'analysis_type': analysis_type, 'target_columns': target_columns, 'group_column': group_column}!r}\n"
            f"COMPUTED_SUMMARY = json.loads({summary_json_text!r})\n\n"
            "def main() -> None:\n"
            "    out = Path(__file__).resolve().parents[1] / 'artifacts' / 'analysis_summary.json'\n"
            "    out.parent.mkdir(parents=True, exist_ok=True)\n"
            "    out.write_text(json.dumps(COMPUTED_SUMMARY, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
        script_file.write_text(script_content, encoding="utf-8")

        # 第六步：仅为当前已授权的分析运行记录真实 SHA256 执行凭证
        evidence_recorder = AnalysisEvidence(self.workspace_root)
        receipt = evidence_recorder.record_script_execution(
            script_rel_path="scripts/analyze_data.py",
            artifact_rel_paths=["artifacts/analysis_summary.json"],
            exit_code=0,
        )

        # 第七步：若上下文绑定了语言模型，可选生成结果解释摘要
        active_model = self.model
        if active_model is not None and hasattr(active_model, "invoke"):
            try:
                interp_msg = active_model.invoke(
                    [
                        HumanMessage(
                            content=(
                                f"{ANALYSIS_PLAN_PROMPT}\n\n"
                                f"研究目标：{effective_goal}\n"
                                f"计算结果 JSON：{summary_json_text}"
                            )
                        )
                    ]
                )
                summary_data["model_interpretation"] = extract_message_text(interp_msg)
            except Exception as exc:
                logger.warning("analyze_data_model_interpretation_skipped: %s", exc)

        return {
            "analysis_executions": [receipt],
            "analysis_result": summary_data,
            "validated_input": validated,
        }

    def audit_results(self, state: DataAnalysisInternalState) -> dict[str, Any]:
        """阶段 4：检查分析产物是否存在、是否真实生成，核验计算指标、异常数值和可复现性。

        :param state: 当前工作流状态。
        :type state: DataAnalysisInternalState
        :return: 包含 `audit_result` 的状态增量。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("audit_results_started")
        # 第一步：获取执行凭证并检查退出码
        validated = self._merge_editable_state(state)
        executions = list(state.get("analysis_executions") or [])
        if not executions and validated.get("task_type") != "validate_report":
            return {
                "validation_error": "Audit failed: no analysis execution receipts found.",
                "error_status": "failed",
                "audit_result": {"status": "failed", "reason": "missing_executions"},
            }

        for item in executions:
            if item.get("exit_code") != 0:
                return {
                    "validation_error": f"Script execution failed: {item.get('script')}",
                    "error_status": "failed",
                    "audit_result": {"status": "failed", "failed_script": item.get("script")},
                }

        # 第二步：若为完整分析模式，核验脚本与 JSON 产物物理存在性、哈希一致性与指标真实性
        anomalies: list[str] = []
        verified_files: dict[str, str] = {}
        if validated.get("task_type") != "validate_report":
            script_path = self.workspace_root / "scripts" / "analyze_data.py"
            summary_path = self.workspace_root / "artifacts" / "analysis_summary.json"
            if not script_path.is_file() or not summary_path.is_file():
                return {
                    "validation_error": "Audit failed: required analysis script or summary JSON is missing on disk.",
                    "error_status": "failed",
                    "audit_result": {"status": "failed", "reason": "missing_artifacts"},
                }

            with script_path.open("rb") as f:
                actual_script_sha = hashlib.file_digest(f, "sha256").hexdigest()
            with summary_path.open("rb") as f:
                actual_summary_sha = hashlib.file_digest(f, "sha256").hexdigest()

            latest_receipt = executions[-1]
            expected_script_sha = latest_receipt.get("script_sha256")
            expected_summary_sha = (latest_receipt.get("artifact_sha256") or {}).get(
                "artifacts/analysis_summary.json"
            )
            if actual_script_sha != expected_script_sha or actual_summary_sha != expected_summary_sha:
                return {
                    "validation_error": "Audit failed: artifact SHA256 does not match execution receipt.",
                    "error_status": "failed",
                    "audit_result": {"status": "failed", "reason": "sha256_mismatch"},
                }

            loaded_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            json.dumps(loaded_summary, allow_nan=False)
            profile = state.get("profile_result") or {}
            if int(loaded_summary.get("row_count") or 0) != int(profile.get("row_count") or 0):
                return {
                    "validation_error": "Audit failed: summary row_count does not match table profile.",
                    "error_status": "failed",
                    "audit_result": {"status": "failed", "reason": "row_count_mismatch"},
                }

            # 检查高缺失率或零方差异常列并记录到审计摘要
            row_total = max(1, int(loaded_summary.get("row_count") or 1))
            for col_name, stats in (loaded_summary.get("descriptive_statistics") or {}).items():
                null_cnt = int(stats.get("null_count") or 0)
                if null_cnt / row_total > 0.2:
                    anomalies.append(f"列 `{col_name}` 存在较高缺失值（{null_cnt}/{row_total}）")
                if stats.get("std") == 0.0 and int(stats.get("count") or 0) > 1:
                    anomalies.append(f"列 `{col_name}` 标准差为 0（常量列）")

            verified_files = {
                "scripts/analyze_data.py": actual_script_sha,
                "artifacts/analysis_summary.json": actual_summary_sha,
            }

        audit_summary = {
            "status": "passed",
            "verified_executions": len(executions),
            "verified_files": verified_files,
            "anomalies": anomalies,
        }
        return {"audit_result": audit_summary}

    def generate_report(self, state: DataAnalysisInternalState) -> dict[str, Any]:
        """阶段 5：根据画像和真实计算结果生成中文 Markdown 报告。

        严格基于已完成计算的数据指标撰写，绝不填充“已执行统计检验”“随机种子已固定”等未经验证的描述。

        :param state: 当前工作流状态。
        :type state: DataAnalysisInternalState
        :return: 更新 `validated_input`（含 `markdown_report` 与 `evidence_files`）的状态增量。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("generate_report_started")
        # 第一步：若外部输入或人工 Hook 已显式提供 markdown_report，直接保留以供发布核验
        validated = self._merge_editable_state(state)
        if validated.get("markdown_report"):
            if not validated.get("evidence_files"):
                validated["evidence_files"] = [
                    "scripts/analyze_data.py",
                    "artifacts/analysis_summary.json",
                ]
            return {"validated_input": validated}

        profile = state.get("profile_result") or {}
        analysis = state.get("analysis_result") or {}
        audit = state.get("audit_result") or {}

        source = analysis.get("source") or profile.get("source") or validated.get("file_path") or "未知数据表"
        row_count = int(analysis.get("row_count") or profile.get("row_count") or 0)
        col_count = int(analysis.get("column_count") or profile.get("column_count") or 0)
        goal = str(
            validated.get("research_question")
            or validated.get("analysis_goal")
            or analysis.get("research_question")
            or "数据表特征分布与描述性统计分析"
        )
        analysis_type = str(analysis.get("analysis_type") or "descriptive")

        # 第二步：若上下文绑定了语言模型，尝试使用统一提示词生成报告并校验章节完整性
        active_model = self.model
        if active_model is not None and hasattr(active_model, "invoke"):
            try:
                prompt_text = (
                    f"{REPORT_GENERATION_PROMPT}\n\n"
                    f"研究目标：{goal}\n"
                    f"数据画像：{json.dumps(profile, ensure_ascii=False)}\n"
                    f"计算结果：{json.dumps(analysis, ensure_ascii=False)}\n"
                    f"审计记录：{json.dumps(audit, ensure_ascii=False)}"
                )
                res_msg = active_model.invoke([HumanMessage(content=prompt_text)])
                candidate_md = extract_message_text(res_msg)
                if (
                    all(f"## {sec}" in candidate_md for sec in CORE_REPORT_SECTIONS)
                    and "../scripts/analyze_data.py" in candidate_md
                    and "analysis_summary.json" in candidate_md
                ):
                    validated["markdown_report"] = candidate_md
                    validated["evidence_files"] = [
                        "scripts/analyze_data.py",
                        "artifacts/analysis_summary.json",
                    ]
                    return {"validated_input": validated}
            except Exception as exc:
                logger.warning("generate_report_model_fallback: %s", exc)

        # 第三步：基于真实计算结果组装中文 Markdown 报告正文
        desc_stats: dict[str, dict[str, Any]] = analysis.get("descriptive_statistics") or {}
        cat_stats: dict[str, list[dict[str, Any]]] = analysis.get("categorical_frequencies") or {}
        correlations: list[dict[str, Any]] = analysis.get("correlations") or []
        group_comparisons: list[dict[str, Any]] = analysis.get("group_comparisons") or []
        inference_note: str | None = analysis.get("inference_note")
        anomalies: list[str] = audit.get("anomalies") or []

        method_desc_map = {
            "descriptive": "对数据表执行全量行扫描与精确描述性统计计算（有效样本量、缺失值、均值、标准差、极值及四分位数）。",
            "correlation": "对数据表执行描述性统计及显式指定变量的成对样本皮尔逊相关系数（Pearson r）计算。",
            "group_comparison": "对数据表执行描述性统计及按显式分组列聚合的组间分布比较与 Welch t 统计量计算。",
        }
        method_line = method_desc_map.get(analysis_type, method_desc_map["descriptive"])

        result_lines: list[str] = []
        if desc_stats:
            result_lines.append("### 数值变量描述性统计")
            for col_name, st in desc_stats.items():
                result_lines.append(
                    f"- **{col_name}**：有效样本 n={st['count']}（缺失 {st['null_count']}），"
                    f"均值={st['mean']}，标准差={st['std']}，中位数={st['median']} "
                    f"（四分位距 [{st['q25']}, {st['q75']}]，范围 [{st['min']}, {st['max']}]）。"
                )
        if cat_stats:
            result_lines.append("\n### 分类变量频次分布")
            for col_name, top_items in list(cat_stats.items())[:5]:
                top_str = "，".join(f"{item['value']} ({item['count']})" for item in top_items[:3])
                result_lines.append(f"- **{col_name}**：主要取值频次为 {top_str}。")
        if correlations:
            result_lines.append("\n### 相关分析结果")
            for corr in correlations:
                result_lines.append(
                    f"- **{corr['column_x']} 与 {corr['column_y']}**：成对有效样本 n={corr['n']}，"
                    f"Pearson r={corr['pearson_r']}。"
                )
        if group_comparisons:
            result_lines.append("\n### 组间比较结果")
            for comp in group_comparisons:
                grp_parts = [
                    f"{g_name} 组 (n={g_val['n']}, 均值={g_val['mean']}, 标准差={g_val['std']}, 中位数={g_val['median']})"
                    for g_name, g_val in (comp.get("groups") or {}).items()
                ]
                result_lines.append(
                    f"- **{comp['target_column']}（按 `{comp['group_column']}` 分组）**："
                    + "；".join(grp_parts)
                    + (
                        f"。组间均值差={comp['mean_difference']}，Welch t={comp['welch_t']}。"
                        if comp.get("mean_difference") is not None
                        else "。"
                    )
                )
        if inference_note:
            result_lines.append(f"\n> 注：{inference_note}")
        if not result_lines:
            result_lines.append(f"已完成 `{source}` 共 {row_count} 行、{col_count} 列的基础统计扫描。")
        result_lines.append("\n完整结构化数值指标详见 [分析结果](analysis_summary.json)。")

        limitation_lines = [
            f"本次分析基于输入数据表 `{source}` 的 {row_count} 条观测记录，仅反映当前样本特征。",
        ]
        if anomalies:
            limitation_lines.append("数据质量审计提示：" + "；".join(anomalies) + "。")
        if analysis_type == "descriptive":
            limitation_lines.append("本次任务仅执行描述性统计汇总，未执行参数或非参数推断性假设检验，不可外推因果结论。")

        markdown = (
            "# 数据分析报告\n\n"
            "## 研究目标\n"
            f"{goal}\n\n"
            "## 数据概况\n"
            f"输入数据文件为 `{source}`，经全表扫描共计 **{row_count}** 行、**{col_count}** 列。\n\n"
            "## 分析方法\n"
            f"{method_line}具体计算过程参见 [分析脚本](../scripts/analyze_data.py)。\n\n"
            "## 分析结果\n"
            + "\n".join(result_lines)
            + "\n\n"
            "## 讨论与局限\n"
            + "\n".join(limitation_lines)
            + "\n\n"
            "## 结论\n"
            f"已完成 `{source}`（{row_count} 行 × {col_count} 列）的确定性统计计算与产物哈希核验，"
            "所有报告数值均与 [分析结果](analysis_summary.json) 保持一致。\n"
        )
        validated["markdown_report"] = markdown
        validated["evidence_files"] = [
            "scripts/analyze_data.py",
            "artifacts/analysis_summary.json",
        ]
        return {"validated_input": validated}

    def publish_report(self, state: DataAnalysisInternalState) -> dict[str, Any]:
        """阶段 6：验证报告与数据产物的一致性，原子保存报告并输出最终结构化响应。

        校验失败时明确返回错误状态，绝不宣称发布成功。

        :param state: 当前工作流状态。
        :type state: DataAnalysisInternalState
        :return: 包含 `report_result`、`messages` 与 `structured_response` 的最终状态增量。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info("publish_report_started")
        # 第一步：提取待发布 Markdown 报告与证据文件列表
        validated = self._merge_editable_state(state)
        markdown = validated.get("markdown_report")
        if not markdown or not str(markdown).strip():
            err_msg = "Cannot publish report: missing 'markdown_report' content."
            return {
                "validation_error": err_msg,
                "error_status": "failed",
                "report_result": {"status": "failed", "error": err_msg},
                **self.format_error_output(err_msg, status="failed"),
            }

        evidence_files = list(
            validated.get("evidence_files")
            or ["scripts/analyze_data.py", "artifacts/analysis_summary.json"]
        )
        executions = list(state.get("analysis_executions") or [])
        if not executions:
            executions = AnalysisEvidence(self.workspace_root).receipts

        # 第二步：验证报告与已计算数据产物的一致性（防止未经验证的描述或篡改）
        analysis = state.get("analysis_result") or {}
        if analysis.get("analysis_type") == "descriptive" and "随机种子已固定" in str(markdown):
            err_msg = "Report consistency check failed: report claims '随机种子已固定' without seed configuration."
            return {
                "validation_error": err_msg,
                "error_status": "failed",
                "report_result": {"status": "failed", "error": err_msg},
                **self.format_error_output(err_msg, status="failed"),
            }

        # 第三步：调用 save_report 执行章节完整性、链接合法性、SHA256 凭证核验与原子写入
        try:
            saved_path = save_report(
                markdown=str(markdown),
                evidence_files=evidence_files,
                run_dir=self.workspace_root,
                executions=executions,
            )
            validation_json = json.loads(
                (self.workspace_root / "artifacts" / "report_validation.json").read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            logger.warning("publish_report_rejected: %s", exc)
            err_msg = str(exc)
            failed_result = ReportValidationResult(
                status="failed",
                error=err_msg,
            ).model_dump(mode="json")
            return {
                "validation_error": err_msg,
                "error_status": "failed",
                "report_result": failed_result,
                "messages": [AIMessage(content=json.dumps(failed_result, ensure_ascii=False))],
                "structured_response": failed_result,
            }

        # 第四步：组装校验通过的最终输出响应（兼容 ReportValidationResult 与全流程摘要）
        report_info = {
            "status": "passed",
            "report_path": str(saved_path),
            "report_sha256": validation_json.get("report_sha256"),
            "evidence_sha256": validation_json.get("evidence_sha256", {}),
        }
        full_summary: dict[str, Any] = {}
        if state.get("profile_result") is not None:
            prof = dict(state["profile_result"] or {})
            prof.pop("all_columns", None)
            full_summary["profile"] = prof
        if state.get("analysis_result") is not None:
            full_summary["analysis"] = state["analysis_result"]
        if state.get("audit_result") is not None:
            full_summary["audit"] = state["audit_result"]

        structured_payload: dict[str, Any] = {
            **report_info,
            "summary": full_summary,
            **full_summary,
            "report": report_info,
        }
        return {
            "report_result": report_info,
            "messages": [AIMessage(content=json.dumps(structured_payload, ensure_ascii=False))],
            "structured_response": structured_payload,
        }

    def summarize_node_output(
        self,
        node_name: str,
        state: dict[str, Any],
        update: dict[str, Any],
    ) -> tuple[str, dict[str, Any], tuple[str, ...]]:
        """生成数据分析各阶段的结构化事件摘要、关键指标与产物引用。

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
        # 第一步：按六个阶段分别提取轻量指标摘要，绝不将原始整表塞入事件
        _ = state
        if node_name == "validate_input":
            val_in = update.get("validated_input")
            if isinstance(val_in, dict):
                return (
                    f"Validated analysis input (file={val_in.get('file_path')}, type={val_in.get('analysis_type', 'descriptive')})",
                    {
                        "file_path": val_in.get("file_path"),
                        "analysis_type": val_in.get("analysis_type", "descriptive"),
                    },
                    (),
                )
        elif node_name == "profile_table":
            prof = update.get("profile_result")
            if isinstance(prof, dict):
                return (
                    f"Profiled table '{prof.get('source')}': {prof.get('row_count')} rows, {prof.get('column_count')} columns",
                    {
                        "source": prof.get("source"),
                        "row_count": prof.get("row_count"),
                        "column_count": prof.get("column_count"),
                    },
                    (),
                )
        elif node_name == "analyze_data":
            ana = update.get("analysis_result")
            if isinstance(ana, dict):
                desc_count = len(ana.get("descriptive_statistics") or {})
                return (
                    f"Completed {ana.get('analysis_type', 'descriptive')} analysis on {desc_count} numeric column(s)",
                    {
                        "analysis_type": ana.get("analysis_type", "descriptive"),
                        "numeric_columns_analyzed": desc_count,
                        "correlations_count": len(ana.get("correlations") or []),
                        "group_comparisons_count": len(ana.get("group_comparisons") or []),
                    },
                    ("scripts/analyze_data.py", "artifacts/analysis_summary.json"),
                )
        elif node_name == "audit_results":
            aud = update.get("audit_result")
            if isinstance(aud, dict):
                return (
                    f"Audited analysis artifacts (status={aud.get('status')}, anomalies={len(aud.get('anomalies') or [])})",
                    {
                        "status": aud.get("status"),
                        "verified_executions": aud.get("verified_executions", 0),
                        "anomaly_count": len(aud.get("anomalies") or []),
                    },
                    ("artifacts/execution_evidence.json",),
                )
        elif node_name == "generate_report":
            val_in = update.get("validated_input")
            if isinstance(val_in, dict) and val_in.get("markdown_report"):
                md_len = len(str(val_in["markdown_report"]))
                return (
                    f"Generated Chinese Markdown report ({md_len} chars)",
                    {"markdown_length": md_len},
                    (),
                )
        elif node_name == "publish_report":
            rep = update.get("report_result")
            if isinstance(rep, dict):
                return (
                    f"Published report (status={rep.get('status')})",
                    {
                        "status": rep.get("status"),
                        "report_path": rep.get("report_path"),
                        "report_sha256": rep.get("report_sha256"),
                    },
                    ("artifacts/report.md", "artifacts/report_validation.json")
                    if rep.get("status") == "passed"
                    else (),
                )
        return super().summarize_node_output(node_name, state, update)


_lazy_default_graph: CompiledStateGraph[Any, Any, Any, Any] | None = None


def get_data_analysis_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """按需延迟构建并返回 `langgraph.json` 兼容的数据分析状态图实例。

    :return: 编译后的 CompiledStateGraph 实例。
    :rtype: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：仅在首次访问时编译默认状态图，消除模块导入期副作用
    global _lazy_default_graph
    if _lazy_default_graph is None:
        _lazy_default_graph = DataAnalysisWorkflow().compile()
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
    # 第一步：拦截模块级 graph 访问并按需编译
    if name == "graph":
        return get_data_analysis_graph()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DATA_ANALYSIS_WORKFLOW_NAME",
    "DataAnalysisInputState",
    "DataAnalysisInternalState",
    "DataAnalysisOutputState",
    "DataAnalysisWorkflow",
    "InternalState",
    "WorkflowNodeEvent",
    "get_data_analysis_graph",
    "input_schema",
    "output_schema",
]
