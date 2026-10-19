"""科研数据分析、表格画像与报告交付的 LangChain 原生工具与 Toolkit 模块。

本模块定义的工具既可供主智能体（Supervisor）在日常监控与直接交互中调用，
也可由 LangGraph 工作流内部节点直接复用。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, BaseToolkit, tool
from langgraph.types import Command, interrupt
from pydantic import ConfigDict, Field

from vertical_plugins.data_analysis.models.profile import TableProfileInput
from vertical_plugins.data_analysis.models.report import ReportValidationInput
from vertical_plugins.data_analysis.prompts import NODE_PROMPTS_MAP, get_node_prompt
from vertical_plugins.data_analysis.tools.reports import save_report
from vertical_plugins.data_analysis.tools.table_profile import profile_table

logger = logging.getLogger(__name__)


def _resolve_target_path(path_str: str, root_dir: Path) -> Path:
    """将虚拟路径或相对路径解析为物理路径。"""
    cleaned = path_str.removeprefix("/workspace/").lstrip("/")
    candidate = Path(cleaned)
    if candidate.is_absolute():
        return candidate.resolve()
    return (root_dir / candidate).resolve()


class DataAnalysisToolkit(BaseToolkit):
    """遵循 LangChain 标准 BaseToolkit 规范的科研数据分析工具包。"""

    workspace_root: Path = Field(
        default_factory=lambda: Path("./.agent-workspace").resolve(),
        description="当前智能体工作区绝对物理根路径",
    )
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, workspace_root: Path | None = None, **kwargs: Any) -> None:
        """初始化科研数据分析工具包。"""
        super().__init__(**kwargs)
        resolved_root = (
            workspace_root or kwargs.get("workspace_root") or Path("./.agent-workspace")
        ).resolve()
        object.__setattr__(self, "workspace_root", resolved_root)

    def get_tools(self) -> list[BaseTool]:
        """导出当前工具包内的所有 LangChain 原生工具实例。

        :return: 包含 get_node_prompt、inspect_table、ask_researcher 与 finalize_report 的工具列表。
        :rtype: list[langchain_core.tools.BaseTool]
        :author: tongliga
        :date: 2026-10-19
        """
        root = self.workspace_root

        @tool(
            "data_analysis_get_node_prompt",
            description=(
                "按节点名称查询工作流中特定节点的系统提示词与执行规范。\n"
                f"可用节点：{list(NODE_PROMPTS_MAP.keys())}"
            ),
        )
        def read_node_prompt(node_name: str) -> str:
            """查询指定分析工作流节点的系统提示词与执行规范。

            :param node_name: 节点名称（如 'parse_and_validate_input', 'profile_table', 'validate_report', 'finalize_output'）。
            :type node_name: str
            :return: 该节点的系统提示词规范文本。
            :rtype: str
            :author: tongliga
            :date: 2026-10-19
            """
            logger.info("read_node_prompt_called", extra={"event_fields": {"node_name": node_name}})
            # 第一步：调用 prompts 模块中的映射查询函数
            return get_node_prompt(node_name)

        @tool(
            "data_analysis_inspect_table",
            args_schema=TableProfileInput,
            description="扫描全表并返回有大小边界的列画像分页，不返回原始整表。",
        )
        def inspect_table(
            file_path: str,
            sheet_name: str | None = None,
            column_offset: int = 0,
        ) -> str:
            """全量扫描 CSV/Parquet/XLSX 数据表并返回紧凑的列画像分页 JSON。

            :param file_path: 当前工作区的文件路径（如 data.csv 或 /workspace/data.csv）。
            :type file_path: str
            :param sheet_name: XLSX 工作表名称；不传时列出工作表供选择。
            :type sheet_name: str | None
            :param column_offset: 从零开始的列分页偏移量。
            :type column_offset: int
            :return: 列画像与行统计的 JSON 字符串。
            :rtype: str
            :author: tongliga
            :date: 2026-10-19
            """
            logger.info(
                "inspect_table_called",
                extra={
                    "event_fields": {
                        "file_path": file_path,
                        "sheet_name": sheet_name,
                        "column_offset": column_offset,
                    }
                },
            )
            # 第一步：解析物理文件路径
            target = _resolve_target_path(file_path, root)
            # 第二步：调用 profile_table 算法全量扫描并提取分页
            result = profile_table(
                file_path=target,
                run_dir=root,
                sheet_name=sheet_name,
                column_offset=column_offset,
            )
            # 第三步：序列化为 JSON 字符串返回
            return json.dumps(result, ensure_ascii=False, allow_nan=False)

        @tool(
            "data_analysis_ask_researcher",
            description="向研究者询问影响分析方法、假设或结论的研究语义，并通过人机协同中断继续。",
        )
        def ask_researcher(question: str) -> str:
            """询问研究者关键语义并在人机协同中断后恢复。

            :param question: 明确简明的研究问题或设计问询。
            :type question: str
            :return: 研究者回复的纯文本。
            :rtype: str
            :author: tongliga
            :date: 2026-10-19
            """
            logger.info(
                "ask_researcher_called", extra={"event_fields": {"question_len": len(question)}}
            )
            # 第一步：触发 LangGraph 人机协同中断等待回复
            reply = interrupt({"kind": "research_question", "question": question})
            return str(reply) if reply is not None else ""

        @tool(
            "data_analysis_finalize_report",
            args_schema=ReportValidationInput,
            description="严格校验完整科研中文报告结构与实际执行凭证，校验通过后原子发布报告。",
        )
        def finalize_report(
            markdown: str,
            evidence_files: list[str],
            runtime: ToolRuntime,
        ) -> Command:
            """校验完整科研报告与执行凭证并发布交付。

            :param markdown: 包含七个指定二级标题的完整中文 Markdown 报告正文。
            :type markdown: str
            :param evidence_files: 相对工作区的脚本与 JSON 结果凭证路径列表。
            :type evidence_files: list[str]
            :param runtime: LangGraph 框架注入的工具调用运行时对象。
            :type runtime: langchain.tools.ToolRuntime
            :return: 包含报告发布路径更新的 Command 对象。
            :rtype: langgraph.types.Command
            :author: tongliga
            :date: 2026-10-19
            """
            logger.info(
                "finalize_report_called",
                extra={"event_fields": {"evidence_files_count": len(evidence_files)}},
            )
            # 第一步：提取状态中保存的真实科学计算执行记录
            state = getattr(runtime, "state", {}) or {}
            executions = state.get("analysis_executions", [])
            # 第二步：调用 save_report 执行结构化校验与原子落盘
            report_path = save_report(markdown, evidence_files, root, executions)
            # 第三步：构造 Command 状态增量与 ToolMessage 响应
            msg_content = f"已校验并成功交付 Markdown 科研分析报告：{report_path}"
            return Command(
                update={
                    "analysis_report_path": str(report_path),
                    "messages": [
                        ToolMessage(content=msg_content, tool_call_id=runtime.tool_call_id)
                    ],
                }
            )

        return [read_node_prompt, inspect_table, ask_researcher, finalize_report]


__all__ = [
    "DataAnalysisToolkit",
]
