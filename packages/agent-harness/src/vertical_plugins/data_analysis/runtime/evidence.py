"""在 Python 科学计算工具外记录科研执行凭证的中间件模块。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from deepagents.graph import DeepAgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.types import Command

logger = logging.getLogger(__name__)


def merge_executions(
    previous: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按执行 ID 合并主、子图凭证，保留唯一记录并维持先后顺序。

    :param previous: 已保存的执行凭证列表。
    :type previous: list[dict[str, typing.Any]]
    :param updates: 本次执行或子图返回的凭证列表。
    :type updates: list[dict[str, typing.Any]]
    :return: 按首次出现顺序排列的唯一凭证列表。
    :rtype: list[dict[str, typing.Any]]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：以 execution_id 为唯一键构造字典并提取值，实现保序去重合并
    merged: dict[str, dict[str, Any]] = {}
    for item in previous + updates:
        execution_id = item.get("execution_id")
        if execution_id:
            merged[execution_id] = item
    return list(merged.values())


class AnalysisState(DeepAgentState):
    """由插件持有并随检查点持久化的科研执行状态。"""

    analysis_executions: Annotated[list[dict[str, Any]], merge_executions]
    analysis_report_path: Annotated[str, lambda previous, current: current]


class AnalysisEvidence(AgentMiddleware[Any, Any, Any]):
    """记录科学计算脚本及其生成产物 SHA256 哈希凭证的运行时中间件。"""

    state_schema = AnalysisState

    def __init__(self, workspace: Path | Any) -> None:
        """初始化科研执行证据中间件。

        :param workspace: 工作区绝对根路径或后端存储对象。
        :type workspace: pathlib.Path | typing.Any
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：规范化提取工作区物理根路径
        if isinstance(workspace, Path):
            self.workdir = workspace.resolve()
            self.backend = None
        elif hasattr(workspace, "workdir"):
            self.workdir = Path(workspace.workdir).resolve()
            self.backend = workspace
        elif hasattr(workspace, "root_dir"):
            self.workdir = Path(workspace.root_dir).resolve()
            self.backend = workspace
        else:
            self.workdir = Path(str(workspace)).resolve()
            self.backend = None
        self.receipts: list[dict[str, Any]] = []
        logger.debug(
            "analysis_evidence_initialized",
            extra={"event_fields": {"workdir": str(self.workdir)}},
        )

    def artifacts(self) -> list[Path]:
        """返回当前工作区边界内的科研产物文件列表。

        :return: 产物文件绝对物理路径列表。
        :rtype: list[pathlib.Path]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：解析工作区内的 artifacts 目录
        directory = self.workdir / "artifacts"
        if not directory.exists() or not directory.is_dir():
            return []
        # 第二步：遍历目录排除凭证文件本身，返回其余产物文件路径列表
        return [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.name != "execution_evidence.json"
        ]

    def save(self, executions: list[dict[str, Any]]) -> None:
        """持久化保存执行凭证到 artifacts/execution_evidence.json。

        :param executions: 本次新增或更新的执行凭证列表。
        :type executions: list[dict[str, typing.Any]]
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：合并内存凭证与新增执行凭证
        self.receipts = merge_executions(self.receipts, executions)
        # 第二步：确保目录存在并将凭证持久化写入 execution_evidence.json
        directory = self.workdir / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "execution_evidence.json"
        path.write_text(
            json.dumps(self.receipts, ensure_ascii=False, allow_nan=False, indent=2),
            encoding="utf-8",
        )
        logger.debug(
            "analysis_evidence_saved",
            extra={"event_fields": {"receipt_count": len(self.receipts)}},
        )

    def _resolve_script_path(self, raw_path: str) -> Path:
        """将脚本参数转换为本地绝对路径。"""
        candidate = Path(raw_path.removeprefix("/workspace/").lstrip("/"))
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.workdir / candidate).resolve()

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步工具调用拦截：为 Python 脚本执行记录凭证。

        :param request: 当前工具调用请求对象。
        :type request: typing.Any
        :param handler: 底层工具执行函数。
        :type handler: typing.Any
        :return: 原工具输出或带状态更新的 Command 对象。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-19
        """
        tool_call = getattr(request, "tool_call", {}) or {}
        tool_name = tool_call.get("name", "")
        # 第一步：非科学计算执行工具直接透传
        if tool_name not in ("run_python", "execute"):
            return handler(request)

        # 第二步：解析目标执行脚本并计算 SHA256 哈希
        args = tool_call.get("args", {})
        script_arg = args.get("script_path") or args.get("command") or ""
        if not script_arg:
            return handler(request)

        script = self._resolve_script_path(script_arg)
        script_hash = ""
        if script.is_file():
            with script.open("rb") as source:
                script_hash = hashlib.file_digest(source, "sha256").hexdigest()

        # 第三步：记录执行前产物快照
        before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in self.artifacts()}
        receipt: dict[str, Any] = {
            "execution_id": uuid4().hex,
            "script": (
                script.relative_to(self.workdir).as_posix()
                if script.is_relative_to(self.workdir)
                else str(script)
            ),
            "script_sha256": script_hash,
            "args": args.get("args", []),
            "artifact_sha256": {},
        }

        # 第四步：调用真实处理器执行并捕获结果
        result = handler(request)

        # 第五步：解析执行输出并更新退出码和日志路径
        try:
            content_str = getattr(result, "content", "")
            output = json.loads(content_str) if isinstance(content_str, str) else {}
            if isinstance(output, dict):
                receipt.update(
                    exit_code=output.get("exit_code", 0),
                    timed_out=output.get("timed_out", False),
                    interrupted=output.get("interrupted", False),
                    stdout_file=output.get("stdout_file"),
                    stderr_file=output.get("stderr_file"),
                )
            else:
                receipt.update(exit_code=0, timed_out=False, interrupted=False)
        except Exception:
            receipt.update(exit_code=0, timed_out=False, interrupted=False)

        # 第六步：比对执行前后产生变动的产物并计算新文件的 SHA256 哈希
        for p in self.artifacts():
            if before.get(p) != (p.stat().st_mtime_ns, p.stat().st_size):
                with p.open("rb") as source:
                    rel_key = (
                        p.relative_to(self.workdir).as_posix()
                        if p.is_relative_to(self.workdir)
                        else str(p)
                    )
                    receipt["artifact_sha256"][rel_key] = hashlib.file_digest(
                        source, "sha256"
                    ).hexdigest()

        # 第七步：持久化保存最新凭证并提交 Command 更新
        self.save([receipt])
        return Command(update={"analysis_executions": [receipt], "messages": [result]})

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步工具调用拦截：为 Python 脚本执行记录凭证。

        :param request: 当前工具调用请求对象。
        :type request: typing.Any
        :param handler: 底层工具异步执行函数。
        :type handler: typing.Any
        :return: 原工具输出或带状态更新的 Command 对象。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-19
        """
        tool_call = getattr(request, "tool_call", {}) or {}
        tool_name = tool_call.get("name", "")
        # 第一步：非科学计算执行工具直接透传
        if tool_name not in ("run_python", "execute"):
            return await handler(request)

        # 第二步：解析目标执行脚本并计算 SHA256 哈希
        args = tool_call.get("args", {})
        script_arg = args.get("script_path") or args.get("command") or ""
        if not script_arg:
            return await handler(request)

        script = self._resolve_script_path(script_arg)
        script_hash = ""
        if script.is_file():
            with script.open("rb") as source:
                script_hash = hashlib.file_digest(source, "sha256").hexdigest()

        # 第三步：记录执行前产物快照
        before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in self.artifacts()}
        receipt: dict[str, Any] = {
            "execution_id": uuid4().hex,
            "script": (
                script.relative_to(self.workdir).as_posix()
                if script.is_relative_to(self.workdir)
                else str(script)
            ),
            "script_sha256": script_hash,
            "args": args.get("args", []),
            "artifact_sha256": {},
        }

        # 第四步：调用真实异步处理器执行并处理取消中断
        try:
            result = await handler(request)
        except asyncio.CancelledError as error:
            receipt.update(exit_code=None, interrupted=True, output=str(error))
            self.save([receipt])
            raise

        # 第五步：解析执行输出并更新退出码
        try:
            content_str = getattr(result, "content", "")
            output = json.loads(content_str) if isinstance(content_str, str) else {}
            if isinstance(output, dict):
                receipt.update(
                    exit_code=output.get("exit_code", 0),
                    timed_out=output.get("timed_out", False),
                    interrupted=output.get("interrupted", False),
                    stdout_file=output.get("stdout_file"),
                    stderr_file=output.get("stderr_file"),
                )
            else:
                receipt.update(exit_code=0, timed_out=False, interrupted=False)
        except Exception:
            receipt.update(exit_code=0, timed_out=False, interrupted=False)

        # 第六步：比对执行前后产生变动的产物并计算新文件的 SHA256 哈希
        for p in self.artifacts():
            if before.get(p) != (p.stat().st_mtime_ns, p.stat().st_size):
                with p.open("rb") as source:
                    rel_key = (
                        p.relative_to(self.workdir).as_posix()
                        if p.is_relative_to(self.workdir)
                        else str(p)
                    )
                    receipt["artifact_sha256"][rel_key] = hashlib.file_digest(
                        source, "sha256"
                    ).hexdigest()

        # 第七步：持久化保存最新凭证并提交 Command 更新
        self.save([receipt])
        return Command(update={"analysis_executions": [receipt], "messages": [result]})


__all__ = [
    "AnalysisEvidence",
    "AnalysisState",
    "merge_executions",
]
