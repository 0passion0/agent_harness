"""科研数据分析确定性 LangGraph 工作流各节点的专用系统提示词、执行规范与分析指南定义。

本模块集中维护工作流固定执行流程中各核心节点的系统级提示词与规约说明，
同时兼容加载原生科研分析指南（guides），为主智能体（Supervisor）监控与图内节点提供清晰透明的语义指导。
"""

from __future__ import annotations

from importlib.resources import files
from typing import Literal

# 工作流核心固定节点名称常量
NODE_INPUT_VALIDATION = "parse_and_validate_input"
NODE_CLARIFY_RESEARCH = "clarify_research"
NODE_PROFILE_TABLE = "profile_table"
NODE_EXECUTE_ANALYSIS = "execute_analysis"
NODE_AUDIT_EVIDENCE = "audit_evidence"
NODE_DRAFT_REPORT = "draft_report"
NODE_VALIDATE_REPORT = "validate_report"
NODE_FINALIZE_OUTPUT = "finalize_output"

# 原生科研分析指南清单与说明
GuideName = Literal[
    "research-analysis",
    "tabular-data",
    "statistical-analysis",
    "supervised-ml",
    "unsupervised-ml",
    "model-interpretation",
    "reporting-export",
]
GUIDE_DESCRIPTIONS: dict[str, str] = {
    "research-analysis": "研究问题、设计澄清、分析范围和复现证据",
    "tabular-data": "CSV/Excel/Parquet全表画像、缺失和大表处理",
    "statistical-analysis": "效应量、置信区间、检验、调整和诊断",
    "supervised-ml": "分类回归、组/时间验证与防止泄漏",
    "unsupervised-ml": "聚类、降维及异常检测",
    "model-interpretation": "模型解释与解释稳定性",
    "reporting-export": "报告章节、证据链接、图表及表格导出",
}

# 1. 入口参数解析与强类型校验节点提示词
INPUT_VALIDATION_NODE_PROMPT = """你正在执行科研数据分析工作流的输入解析与契约校验节点（parse_and_validate_input）。
核心职责与约束规范：
1. 从传入参数或最新用户消息中提取待分析数据文件路径（file_path）、XLSX工作表（sheet_name）、列分页偏移量（column_offset）或中文科研报告（markdown_report）与凭据文件列表（evidence_files）。
2. 使用 Pydantic 强类型模型严格校验输入载荷；若字段缺失或格式非法，立即终止并暴露明确的校验错误原因（Fail-Fast）。
3. 校验通过后，格式化受保护的 validated_input 字典供下游固定节点消费。
"""

# 2. 研究问题与分析设计澄清节点提示词
CLARIFY_RESEARCH_NODE_PROMPT = """你正在执行科研数据分析工作流的研究设计与指南澄清节点（clarify_research）。
核心职责与约束规范：
1. 依据任务目标加载对应的科研分析规范（如 research-analysis 或 tabular-data），明确分析边界与因变量/自变量定义。
2. 识别缺失的关键研究语义或假设前提；必要时可通过人机协同中断（ask_researcher）向研究者澄清，不主观臆造分析目标。
"""

# 3. 表格全量行扫描与受限列画像节点提示词
PROFILE_NODE_PROMPT = """你正在执行科研数据分析工作流的数据表画像分析节点（profile_table）。
核心职责与约束规范：
1. 基于 DuckDB / OpenPyXL 对 CSV、Parquet 或 XLSX 文件执行全量行扫描（全表行数、真实缺失率计算）。
2. 提取具有大小边界的列元数据分页（默认单页上限 50 列，支持 column_offset 偏移），严禁将整张原始大表直接载入上下文或输出。
3. 针对数值列提取五数概括（极值、中位数、四分位数、均值、标准差）与离群值比率；针对分类列提取 Top-N 频次分布。
4. 确保所有统计指标客观真实，禁止臆造数据特征。
"""

# 4. 统计推断与模型计算节点提示词
EXECUTE_ANALYSIS_NODE_PROMPT = """你正在执行科研数据分析工作流的统计计算与模型执行节点（execute_analysis）。
核心职责与约束规范：
1. 编写或调度可复现的 Python 科学计算脚本（存放在 scripts/ 目录），执行统计检验、效应量计算或机器学习建模。
2. 将量化结果与指标原子输出至 artifacts/*.json，图表输出至 artifacts/*.png。
3. 记录执行退出码并捕获脚本与产物的 SHA256 哈希凭证，严禁无凭据的幻觉数据。
"""

# 5. 执行凭证与数据泄漏独立审计节点提示词
AUDIT_EVIDENCE_NODE_PROMPT = """你正在执行科研数据分析工作流的凭据审查与防泄漏审计节点（audit_evidence）。
核心职责与约束规范：
1. 核验所有执行记录与产物哈希，确保计算脚本实际成功执行（exit_code=0）且结果文件非空、实数有限。
2. 审查统计检验假设、交叉验证分组是否发生数据泄漏（Data Leakage），保障科研可复现性。
"""

# 6. 报告草拟生成节点提示词
DRAFT_REPORT_NODE_PROMPT = """你正在执行科研数据分析工作流的报告草拟节点（draft_report）。
核心职责与约束规范：
1. 依据真实执行凭证与全表画像指标，草拟包含 7 大必需中文二级标题的研究报告：
   - "## 研究问题"
   - "## 数据说明"
   - "## 分析方法"
   - "## 结果"
   - "## 讨论与局限"
   - "## 结论"
   - "## 复现信息"
2. 确保正文所有关键数字均以相对路径显式链接真实凭据文件（如 [分析脚本](../scripts/...)、[指标结果](...)）。
"""

# 7. 科研分析报告结构与 SHA256 凭据审计节点提示词
VALIDATE_REPORT_NODE_PROMPT = """你正在执行科研数据分析工作流的报告校验与发布节点（validate_report）。
核心职责与约束规范：
1. 严格核验科研报告正文是否包含 7 个必需中文二级标题，且各段落包含实质学术内容。
2. 提取报告正文中引用的所有产物文件名，与声明的 evidence_files 列表双向对齐，缺一不可。
3. 对所有证据文件进行 SHA256 内容摘要核验，确保与中间件拦截的真实执行记录一致，杜绝未经计算的虚假产物。
4. 校验通过后，执行原子落盘发布至 artifacts/report.md 与 artifacts/report_validation.json。
"""

# 8. 工作流收尾与交付节点提示词
FINALIZE_OUTPUT_NODE_PROMPT = """你正在执行科研数据分析工作流的最终出口交付节点（finalize_output）。
核心职责与约束规范：
1. 依据前期节点流转结果，组装符合 output_schema 契约的标准响应字典 structured_response。
2. 若存在校验错误或执行异常，输出状态为 invalid_input 或 failed 的错误结构，并附带可排查的错误描述。
3. 若分析成功，输出 status="passed"，并合并 profile 与 report 的结构化产物。
4. 生成对主智能体（Supervisor）友好且易于监控排查的最终 AIMessage 消息。
"""

# 节点提示词字典映射表
NODE_PROMPTS_MAP: dict[str, str] = {
    NODE_INPUT_VALIDATION: INPUT_VALIDATION_NODE_PROMPT,
    NODE_CLARIFY_RESEARCH: CLARIFY_RESEARCH_NODE_PROMPT,
    NODE_PROFILE_TABLE: PROFILE_NODE_PROMPT,
    NODE_EXECUTE_ANALYSIS: EXECUTE_ANALYSIS_NODE_PROMPT,
    NODE_AUDIT_EVIDENCE: AUDIT_EVIDENCE_NODE_PROMPT,
    NODE_DRAFT_REPORT: DRAFT_REPORT_NODE_PROMPT,
    NODE_VALIDATE_REPORT: VALIDATE_REPORT_NODE_PROMPT,
    NODE_FINALIZE_OUTPUT: FINALIZE_OUTPUT_NODE_PROMPT,
}


def read_analysis_guide(name: str) -> str:
    """读取插件内置的只读分析规范指南 Markdown。

    :param name: 指南名称（如 'research-analysis', 'tabular-data' 等）。
    :type name: str
    :return: 对应的指南完整 Markdown 文本。
    :rtype: str
    :raises KeyError: 当指南名称不存在时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    guide_clean = name.removesuffix(".md")
    try:
        # ponytail: 使用标准库 importlib.resources 安全读取包内指南资源
        return (
            files("data_analysis")
            .joinpath("guides", f"{guide_clean}.md")
            .read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise KeyError(
            f"未知的分析规范指南：{name!r}。可用指南：{list(GUIDE_DESCRIPTIONS.keys())}"
        ) from exc


def get_node_prompt(node_name: str) -> str:
    """按节点名称获取对应的系统提示词与执行规范，若为指南名称则返回指南内容。

    :param node_name: 工作流节点名称或分析指南名称。
    :type node_name: str
    :return: 对应的节点系统提示词或指南正文。
    :rtype: str
    :raises KeyError: 当传入未知名称时抛出 KeyError。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：若匹配固定工作流节点，直接返回对应提示词
    if node_name in NODE_PROMPTS_MAP:
        return NODE_PROMPTS_MAP[node_name]
    # 第二步：若匹配原生分析指南，读取并返回指南文档
    if node_name in GUIDE_DESCRIPTIONS or node_name.removesuffix(".md") in GUIDE_DESCRIPTIONS:
        return read_analysis_guide(node_name)
    # 第三步：未命中则快速失败
    raise KeyError(
        f"未知的分析节点或指南：{node_name!r}。可用节点列表：{list(NODE_PROMPTS_MAP.keys())}"
    )


__all__ = [
    "AUDIT_EVIDENCE_NODE_PROMPT",
    "CLARIFY_RESEARCH_NODE_PROMPT",
    "DRAFT_REPORT_NODE_PROMPT",
    "EXECUTE_ANALYSIS_NODE_PROMPT",
    "FINALIZE_OUTPUT_NODE_PROMPT",
    "GUIDE_DESCRIPTIONS",
    "GuideName",
    "INPUT_VALIDATION_NODE_PROMPT",
    "NODE_AUDIT_EVIDENCE",
    "NODE_CLARIFY_RESEARCH",
    "NODE_DRAFT_REPORT",
    "NODE_EXECUTE_ANALYSIS",
    "NODE_FINALIZE_OUTPUT",
    "NODE_INPUT_VALIDATION",
    "NODE_PROFILE_TABLE",
    "NODE_PROMPTS_MAP",
    "NODE_VALIDATE_REPORT",
    "PROFILE_NODE_PROMPT",
    "VALIDATE_REPORT_NODE_PROMPT",
    "get_node_prompt",
    "read_analysis_guide",
]
