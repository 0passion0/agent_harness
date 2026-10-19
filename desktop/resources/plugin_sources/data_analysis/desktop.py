"""数据分析插件在桌面应用中的状态映射。"""

from pathlib import Path

from medpeer_worker.files import WorkspaceBackend


DESCRIPTOR = {
    'id': 'data_analysis',
    'name': '科研数据分析',
    'description': '特定科研任务下提供研究问答、分析证据和报告交付。',
    'interaction_kinds': ['research_question'],
}


def snapshot(state_values: dict, backend: WorkspaceBackend) -> dict:
    """将科研分析状态转换为桌面可读取的投影。

    :param state_values: 当前 LangGraph 状态字典。
    :type state_values: dict
    :param backend: 当前工作区存储后端。
    :type backend: WorkspaceBackend
    :return: 桌面端可用的插件状态快照字典。
    :rtype: dict
    :author: tongliga
    :date: 2026-10-16
    """
    # 第一步：提取科研插件相关的状态键值（激活状态、执行记录、报告路径）
    result = {key: state_values[key] for key in
              ('active_plugin', 'analysis_executions', 'analysis_report_path') if key in state_values}

    # 第二步：如果存在分析报告，计算其相对工作区的虚拟路径并挂载到 snapshot 中
    if 'analysis_report_path' in result:
        report = Path(result['analysis_report_path']).resolve()
        if report.is_relative_to(backend.cwd) and report.is_file():
            virtual = '/workspace/' + report.relative_to(backend.cwd).as_posix()
            backend.tool_path(virtual)
            result['report_open_path'] = virtual
    return result


def artifact_paths(state_values: dict, backend: WorkspaceBackend) -> list[str]:
    """列出科研分析插件本轮发布的报告与数据产物。

    :param state_values: 当前图状态字典。
    :type state_values: dict
    :param backend: 当前工作区存储后端。
    :type backend: WorkspaceBackend
    :return: 发布的产物虚拟路径列表。
    :rtype: list[str]
    :author: tongliga
    :date: 2026-10-16
    """
    # 第一步：判断当前激活插件是否为 data_analysis，若不是则直接返回空
    if state_values.get('active_plugin') != 'data_analysis':
        return []

    # 第二步：获取快照中的报告打开路径并包装为产物列表返回
    report = snapshot(state_values, backend).get('report_open_path')
    return [report] if report else []


