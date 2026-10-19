"""科研数据分析确定性 LangGraph 工作流各节点的专用系统提示词与规范定义。

本模块集中维护工作流固定执行流程中各核心节点的系统级提示词与规约说明，
避免分散的多提示词碎片文件，为主智能体（Supervisor）监控与图内节点提供清晰透明的语义指导。
"""

from __future__ import annotations

# 工作流各固定节点名称常量
NODE_INPUT_VALIDATION = "parse_and_validate_input"
NODE_PROFILE_TABLE = "profile_table"
NODE_VALIDATE_REPORT = "validate_report"
NODE_FINALIZE_OUTPUT = "finalize_output"

# 1. 入口参数解析与强类型校验节点提示词
INPUT_VALIDATION_NODE_PROMPT = """你正在执行科研数据分析工作流的输入解析与契约校验节点（parse_and_validate_input）。
核心职责与约束规范：
1. 从传入参数或最新用户消息中提取待分析数据文件路径（file_path）、XLSX工作表（sheet_name）、列分页偏移量（column_offset）或中文科研报告（markdown_report）与凭据文件列表（evidence_files）。
2. 使用 Pydantic 强类型模型严格校验输入载荷；若字段缺失或格式非法，立即终止并暴露明确的校验错误原因（Fail-Fast）。
3. 校验通过后，格式化受保护的 validated_input 字典供下游固定节点消费。
"""

# 2. 表格全量行扫描与受限列画像节点提示词
PROFILE_NODE_PROMPT = """你正在执行科研数据分析工作流的数据表画像分析节点（profile_table）。
核心职责与约束规范：
1. 基于 DuckDB / OpenPyXL 对 CSV、Parquet 或 XLSX 文件执行全量行扫描（全表行数、真实缺失率计算）。
2. 提取具有大小边界的列元数据分页（默认单页上限 50 列，支持 column_offset 偏移），严禁将整张原始大表直接载入上下文或输出。
3. 针对数值列提取五数概括（极值、中位数、四分位数、均值、标准差）与离群值比率；针对分类列提取 Top-N 频次分布。
4. 确保所有统计指标客观真实，禁止臆造数据特征。
"""

# 3. 科研分析报告结构与 SHA256 凭据审计节点提示词
VALIDATE_REPORT_NODE_PROMPT = """你正在执行科研数据分析工作流的报告校验与发布节点（validate_report）。
核心职责与约束规范：
1. 严格核验科研报告正文是否包含 7 个必需中文二级标题：
   - "## 研究问题"
   - "## 数据说明"
   - "## 分析方法"
   - "## 结果"
   - "## 讨论与局限"
   - "## 结论"
   - "## 复现信息"
2. 提取报告正文中引用的所有产物文件名（如 artifacts/*.json、artifacts/*.png），与声明的 evidence_files 列表双向对齐，缺一不可。
3. 对所有证据文件进行 SHA256 内容摘要核验，确保与中间件拦截的真实执行记录（execution receipts）一致，杜绝未经计算的虚假产物。
4. 校验通过后，执行原子落盘发布至 artifacts/analysis_report.md。
"""

# 4. 工作流收尾与交付节点提示词
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
    NODE_PROFILE_TABLE: PROFILE_NODE_PROMPT,
    NODE_VALIDATE_REPORT: VALIDATE_REPORT_NODE_PROMPT,
    NODE_FINALIZE_OUTPUT: FINALIZE_OUTPUT_NODE_PROMPT,
}


def get_node_prompt(node_name: str) -> str:
    """按节点名称获取对应的系统提示词与执行规范。

    :param node_name: 工作流节点名称。
    :type node_name: str
    :return: 对应的节点系统提示词字符串。
    :rtype: str
    :raises KeyError: 当传入未知节点名称时抛出 KeyError。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：检查节点是否存在于已定义的映射表中
    if node_name not in NODE_PROMPTS_MAP:
        raise KeyError(
            f"未知的分析节点：{node_name!r}。可用节点列表：{list(NODE_PROMPTS_MAP.keys())}"
        )
    # 第二步：返回该节点的系统规范提示词
    return NODE_PROMPTS_MAP[node_name]


__all__ = [
    "FINALIZE_OUTPUT_NODE_PROMPT",
    "INPUT_VALIDATION_NODE_PROMPT",
    "NODE_FINALIZE_OUTPUT",
    "NODE_INPUT_VALIDATION",
    "NODE_PROFILE_TABLE",
    "NODE_PROMPTS_MAP",
    "NODE_VALIDATE_REPORT",
    "PROFILE_NODE_PROMPT",
    "VALIDATE_REPORT_NODE_PROMPT",
    "get_node_prompt",
]
