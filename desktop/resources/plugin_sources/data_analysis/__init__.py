"""科研数据分析插件：工具、分析规范、执行凭证和隔离子智能体。"""

import json
from importlib.resources import files
from typing import Literal

from deepagents.middleware.summarization import SummarizationMiddleware, compute_summarization_defaults
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from medpeer_worker.plugins import PluginContribution
from .evidence import AnalysisEvidence, AnalysisState
from .reports import save_report


GuideName = Literal['research-analysis', 'tabular-data', 'statistical-analysis', 'supervised-ml',
                    'unsupervised-ml', 'model-interpretation', 'reporting-export']
GUIDE_DESCRIPTIONS = {
    'research-analysis': '研究问题、设计澄清、分析范围和复现证据',
    'tabular-data': 'CSV/Excel/Parquet全表画像、缺失和大表处理',
    'statistical-analysis': '效应量、置信区间、检验、调整和诊断',
    'supervised-ml': '分类回归、组/时间验证与防止泄漏',
    'unsupervised-ml': '聚类、降维及异常检测',
    'model-interpretation': '模型解释与解释稳定性',
    'reporting-export': '报告章节、证据链接、图表及表格导出',
}


def build(context) -> PluginContribution:
    """将科研能力接入当前会话，不构建另一套模型或执行环境。

    :param context: 当前 Agent 的模型、工作区、守卫和本地工具上下文。
    :type context: PluginContext
    :return: 插件工具、中间件及四种隔离子智能体的装配贡献。
    :rtype: PluginContribution
    :raises ImportError: 表格画像必需依赖没有安装。
    :raises ValueError: 当前会话没有可用 Python 运行时。
    :author: tongliga
    :date: 2026-10-16
    """
    # 第一步：校验是否存在可用的 Python 运行时，若缺失则快速失败
    if context.guard.runtimes.python is None:
        raise ValueError('数据分析需要可用的 Python 运行时')
    from medpeer_worker.agent import LiveMemory

    # 第二步：获取工作区路径与证据追踪器
    backend, guard = context.backend, context.guard
    output_path = '/workspace' + backend._to_virtual_path(backend.workdir)
    evidence = AnalysisEvidence(backend)

    # 第三步：定义插件内置的专用分析工具集
    @tool(description='按需读取一份插件内部分析规范，使用前按任务选择，不要一次读取全部。\n' + '\n'.join(f'{name}: {description}' for name, description in GUIDE_DESCRIPTIONS.items()))
    def read_analysis_guide(name: GuideName) -> str:
        """读取插件内部的只读分析规范；这些资源不是用户 Skills。

        :param name: 需要读取的分析规范名称。
        :return: 该规范的完整文本。
        """
        # 读取插件内置的规范 markdown 文件
        return files(__package__).joinpath('guides', name + '.md').read_text(encoding='utf-8')

    @tool
    def inspect_table(file_path: str, sheet_name: str | None = None, column_offset: int = 0) -> str:
        """扫描全表并返回有大小边界的列画像，不返回原始整表。

        :param file_path: 当前工作区的 /workspace/ 文件路径。
        :param sheet_name: XLSX 工作表；不传时只列出工作表供选择。
        :param column_offset: 从零开始的列分页偏移。
        :return: 全表行数、缺失、类型、分布及下一列页的 JSON。
        """
        # 解析画像产物存储目录
        backend.resolve_path(backend._to_virtual_path(backend.workdir / 'artifacts' / 'table_profiles'))
        try:
            from .table_profile import profile_table
            return json.dumps(profile_table(backend.tool_path(file_path), backend.workdir, sheet_name, column_offset), ensure_ascii=False, allow_nan=False)
        except ImportError as error:
            return f'表格分析缺少 Python 依赖：{error}；请安装 requirements-desktop.txt。'

    @tool
    def ask_researcher(question: str) -> str:
        """询问影响方法或结论的研究语义，并在当前会话中继续。

        :param question: 简短、明确的研究问题、数据或设计问询。
        :return: 研究者通过桌面交互输入的回答。
        """
        # 中断图执行等待人机协同回复
        return interrupt({'kind': 'research_question', 'question': question})

    @tool
    def finalize_report(markdown: str, evidence_files: list[str], runtime: ToolRuntime) -> Command:
        """校验完整报告与实际执行证据，然后交付 Markdown。

        :param markdown: 包含七个指定二级标题的完整中文报告。
        :param evidence_files: 相对当前产物根目录的脚本、JSON 和图表路径。
        :param runtime: 框架注入的当前图状态和工具调用 ID。
        :return: 已校验报告路径及图状态更新。
        """
        # 校验并持久化保存报告
        report = save_report(markdown, evidence_files, backend.workdir, runtime.state.get('analysis_executions', []))
        return Command(update={'analysis_report_path': str(report), 'messages': [
            ToolMessage(content=f'已校验并交付 Markdown 报告：{report}', tool_call_id=runtime.tool_call_id),
        ]})


    # 第四步：加载系统级提示词模板与子智能体通用约束
    common_prompt = files(__package__).joinpath('prompts/common.md').read_text(encoding='utf-8').format(
        output_path=output_path, workdir=backend.workdir, python=guard.runtimes.python)
    main_prompt = files(__package__).joinpath('prompts/main.md').read_text(encoding='utf-8')
    tools = [read_analysis_guide, inspect_table, ask_researcher, finalize_report]
    memory = ['/workspace/.medpeer/AGENTS.md', '/workspace/.medpeer/agent.md']
    child_tools = [*context.local_tools, read_analysis_guide, inspect_table, ask_researcher]

    # 第五步：组装四大隔离科研子智能体（general-purpose, data-analyst, reviewer, report-writer）
    subagents = [
        {'name': name, 'description': description, 'mode': 'isolated', 'model': context.model,
         'tools': child_tools, 'system_prompt': common_prompt + files(__package__).joinpath('prompts', name + '.md').read_text(encoding='utf-8'),
         'skills': ['/workspace/.medpeer/skills/'] if backend.resolve_path('/.medpeer/skills/').is_dir() else [],
         'middleware': [guard, LiveMemory(backend=guard.backend, sources=memory),
                        SummarizationMiddleware(model=context.model, backend=guard.backend, **compute_summarization_defaults(context.model), trim_tokens_to_summarize=None), evidence]}
        for name, description in (
            ('general-purpose', '完成科研分析中边界明确的小任务：定向调查、脚本修正、图表或结果整理。'),
            ('data-analyst', '检查全表并执行研究相关统计或机器学习，生成脚本与 JSON 证据。'),
            ('reviewer', '独立核对指定脚本、结果和解释，检查设计、缺失、泄漏及关键数字。'),
            ('report-writer', '依据已确认语义和真实执行证据整理报告草稿，返回草稿路径。'),
        )
    ]
    return PluginContribution(tools=tools, middleware=[evidence], system_prompt=main_prompt, subagents=subagents)

