"""在当前脚本工具外记录科研执行证据，不替换进程执行实现。"""

import asyncio
import hashlib
import json
from typing import Annotated
from uuid import uuid4

from deepagents.graph import DeepAgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.types import Command


def merge_executions(previous: list[dict], updates: list[dict]) -> list[dict]:
    """按执行 ID 合并主、子图凭证，保留真正的重复执行。

    :param previous: 已保存的执行凭证列表。
    :type previous: list[dict]
    :param updates: 本次执行或子图返回的凭证列表。
    :type updates: list[dict]
    :return: 按首次出现顺序排列的唯一凭证列表。
    :rtype: list[dict]
    :author: tongliga
    :date: 2026-10-16
    """
    # 第一步：以 execution_id 为唯一键构造字典并提取值，实现保序去重合并
    return list({item['execution_id']: item for item in previous + updates}.values())


class AnalysisState(DeepAgentState):
    """由插件持有并随当前 SQLite 检查点保存的科研状态。

    :ivar analysis_executions: 记录的所有 Python 脚本分析执行凭证列表。
    :vartype analysis_executions: Annotated[list[dict], merge_executions]
    :ivar analysis_report_path: 最终生成的科研分析报告虚拟路径。
    :vartype analysis_report_path: Annotated[str, lambda previous, current: current]
    :author: tongliga
    :date: 2026-10-16
    """

    analysis_executions: Annotated[list[dict], merge_executions]
    analysis_report_path: Annotated[str, lambda previous, current: current]


class AnalysisEvidence(AgentMiddleware):
    """在会话守卫的串行锁内采集脚本及本次变动产物的凭证。

    :author: tongliga
    :date: 2026-10-16
    """

    state_schema = AnalysisState

    def __init__(self, backend):
        """绑定当前产物目录和会话内的共享凭证集合。

        :param backend: 当前工作区存储后端。
        :type backend: WorkspaceBackend
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-16
        """
        # 第一步：初始化工作区后端与本地内存凭证列表
        self.backend = backend
        self.receipts = []

    def artifacts(self):
        """返回当前工作区边界内的科研产物文件。

        :return: 以产物目录为根的文件路径列表。
        :rtype: list[Path]
        :raises ValueError: 产物路径经链接解析后越过工作区。
        :author: tongliga
        :date: 2026-10-16
        """
        # 第一步：解析工作区内的 artifacts 目录
        directory = self.backend.resolve_path(self.backend._to_virtual_path(self.backend.workdir / 'artifacts'))
        if not directory.exists():
            return []
        # 第二步：遍历目录排除凭证文件本身，返回其余产物文件路径列表
        return [self.backend.resolve_path(self.backend._to_virtual_path(path))
                for path in directory.rglob('*') if path.is_file() and path.name != 'execution_evidence.json']

    def save(self, executions):
        """先保存实际凭证，再向图返回状态更新。

        :param executions: 本次确定的执行凭证列表。
        :type executions: list[dict]
        :return: 无返回值。
        :rtype: None
        :raises OSError: 凭证文件不能持久化。
        :author: tongliga
        :date: 2026-10-16
        """
        # 第一步：合并内存凭证与新增执行凭证
        self.receipts = merge_executions(self.receipts, executions)
        # 第二步：确保目录存在并将凭证持久化写入 execution_evidence.json
        path = self.backend.resolve_path(self.backend._to_virtual_path(self.backend.workdir / 'artifacts' / 'execution_evidence.json'))
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(self.receipts, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')

    async def awrap_tool_call(self, request, handler):
        """为真实 run_python 执行附加凭证，保留原工具消息。

        :param request: 当前原生工具请求对象。
        :type request: ToolCallRequest
        :param handler: 当前进程工具的真实异步执行器。
        :type handler: Callable
        :return: 原工具消息或带凭证更新的 Command 对象。
        :rtype: Any
        :raises asyncio.CancelledError: 执行被取消；记录中断后继续传播。
        :author: tongliga
        :date: 2026-10-16
        """
        # 第一步：非 run_python 工具调用直接透传执行
        if request.tool_call['name'] != 'run_python':
            return await handler(request)

        # 第二步：解析目标执行脚本并计算 SHA256 哈希摘要
        script = self.backend.tool_path(request.tool_call['args']['script_path'])
        with script.open('rb') as source:
            script_hash = hashlib.file_digest(source, 'sha256').hexdigest()

        # 第三步：记录执行前产物快照（修改时间与大小）
        before = {path: (path.stat().st_mtime_ns, path.stat().st_size) for path in self.artifacts()}
        receipt = {
            'execution_id': uuid4().hex,
            'script': script.relative_to(self.backend.workdir).as_posix() if script.is_relative_to(self.backend.workdir) else str(script),
            'script_sha256': script_hash,
            'args': request.tool_call['args'].get('args', []),
            'artifact_sha256': {},
        }
        self.receipts = merge_executions(self.receipts, request.state.get('analysis_executions', []))

        # 第四步：调用真实处理器执行脚本，若被取消则记录异常凭证并向外抛出
        try:
            result = await handler(request)
        except asyncio.CancelledError as error:
            receipt.update(exit_code=None, interrupted=True, output=str(error))
            self.save([receipt])
            raise

        # 第五步：解析执行输出并更新退出码和日志路径
        output = json.loads(result.content)
        receipt.update(exit_code=output['exit_code'], timed_out=output['timed_out'], interrupted=output['interrupted'],
                       stdout_file=output['stdout_file'], stderr_file=output['stderr_file'])

        # 第六步：比对执行前后产生变动的产物并计算新文件的 SHA256 哈希
        for path in self.artifacts():
            if before.get(path) != (path.stat().st_mtime_ns, path.stat().st_size):
                with path.open('rb') as source:
                    receipt['artifact_sha256'][path.relative_to(self.backend.workdir).as_posix()] = hashlib.file_digest(source, 'sha256').hexdigest()

        # 第七步：持久化保存最新凭证并向图状态提交 Command 更新
        self.save([receipt])
        return Command(update={'analysis_executions': [receipt], 'messages': [result]})

