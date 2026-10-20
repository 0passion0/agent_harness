"""科学计算执行凭证、产物哈希核验与状态持久化模型模块。

仅对已授权的数据分析工作流运行进行执行证据记录，不再作为全局 AgentMiddleware 注入主 Agent。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from typing_extensions import TypedDict

from vertical_plugins.common.contracts import StrictModel

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
    for item in (previous or []) + (updates or []):
        execution_id = item.get("execution_id")
        if execution_id:
            merged[str(execution_id)] = item
    return list(merged.values())


class AnalysisState(TypedDict, total=False):
    """由数据分析工作流持有的执行凭证状态字典。"""

    analysis_executions: Annotated[list[dict[str, Any]], merge_executions]
    analysis_report_path: Annotated[str, lambda previous, current: current]


class AnalysisEvidence:
    """仅针对已授权数据分析运行记录脚本及产物 SHA256 哈希凭证的记录器。"""

    state_schema = AnalysisState

    def __init__(self, workspace: Path | Any) -> None:
        """初始化科研执行证据记录器。

        :param workspace: 工作区绝对根路径或包含 workdir/root_dir 的对象。
        :type workspace: pathlib.Path | typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：规范化提取工作区物理根路径
        if isinstance(workspace, Path):
            self.workdir = workspace.resolve()
        elif hasattr(workspace, "workdir"):
            self.workdir = Path(workspace.workdir).resolve()
        elif hasattr(workspace, "root_dir"):
            self.workdir = Path(workspace.root_dir).resolve()
        else:
            self.workdir = Path(str(workspace)).resolve()
        self.receipts: list[dict[str, Any]] = []
        evidence_file = self.workdir / "artifacts" / "execution_evidence.json"
        if evidence_file.is_file():
            try:
                loaded = json.loads(evidence_file.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    self.receipts = [item for item in loaded if isinstance(item, dict)]
            except Exception:
                self.receipts = []

    def save(self, executions: list[dict[str, Any]]) -> None:
        """持久化保存执行凭证到 `artifacts/execution_evidence.json`。

        :param executions: 本次新增或更新的执行凭证列表。
        :type executions: list[dict[str, typing.Any]]
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：合并已有凭证与新增执行凭证
        self.receipts = merge_executions(self.receipts, executions)
        # 第二步：确保目录存在并将凭证持久化写入 execution_evidence.json
        directory = self.workdir / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "execution_evidence.json"
        path.write_text(
            json.dumps(self.receipts, ensure_ascii=False, allow_nan=False, indent=2),
            encoding="utf-8",
        )

    def record_script_execution(
        self,
        *,
        script_rel_path: str,
        artifact_rel_paths: list[str],
        exit_code: int = 0,
        args: list[Any] | None = None,
    ) -> dict[str, Any]:
        """计算脚本与产物真实 SHA256 哈希并持久化执行凭证。

        :param script_rel_path: 相对工作区根目录的脚本路径（如 `scripts/analyze_data.py`）。
        :type script_rel_path: str
        :param artifact_rel_paths: 相对工作区根目录的产物路径列表（如 `['artifacts/analysis_summary.json']`）。
        :type artifact_rel_paths: list[str]
        :param exit_code: 脚本退出码，默认 0。
        :type exit_code: int
        :param args: 执行参数列表。
        :type args: list[typing.Any] | None
        :return: 持久化后的执行凭证字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：计算脚本文件 SHA256
        script_file = (self.workdir / script_rel_path).resolve()
        with script_file.open("rb") as f:
            script_sha = hashlib.file_digest(f, "sha256").hexdigest()

        # 第二步：计算所有指定产物文件的 SHA256
        artifact_hashes: dict[str, str] = {}
        for rel_path in artifact_rel_paths:
            art_file = (self.workdir / rel_path).resolve()
            if art_file.is_file():
                with art_file.open("rb") as f:
                    artifact_hashes[rel_path] = hashlib.file_digest(f, "sha256").hexdigest()

        # 第三步：构造凭证并落盘
        receipt: dict[str, Any] = {
            "execution_id": uuid4().hex,
            "script": script_rel_path,
            "script_sha256": script_sha,
            "args": list(args or []),
            "artifact_sha256": artifact_hashes,
            "exit_code": exit_code,
            "timed_out": False,
            "interrupted": False,
        }
        self.save([receipt])
        return receipt


def snapshot(values: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """生成用于客户端展示的科研分析执行状态快照。

    :param values: 检查点状态字典。
    :type values: dict[str, typing.Any]
    :param workspace: 工作区根目录物理路径。
    :type workspace: pathlib.Path
    :return: 包含执行记录与报告虚拟路径的快照字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：提取执行记录与报告物理路径并映射为虚拟路径
    executions = list(values.get("analysis_executions") or [])
    report_path = values.get("analysis_report_path")
    report_open_path: str | None = None
    if report_path:
        try:
            rel = Path(str(report_path)).resolve().relative_to(workspace.resolve())
            report_open_path = "/workspace/" + str(rel).replace("\\", "/")
        except ValueError:
            report_open_path = None
    return {
        "analysis_executions": executions,
        "report_open_path": report_open_path,
    }


def artifact_paths(values: dict[str, Any], workspace: Path) -> list[str]:
    """提取当前激活插件生成的虚拟工作区产物路径列表。

    :param values: 检查点状态字典。
    :type values: dict[str, typing.Any]
    :param workspace: 工作区根目录物理路径。
    :type workspace: pathlib.Path
    :return: 虚拟路径字符串列表。
    :rtype: list[str]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：若当前激活插件不是 data_analysis 则返回空列表
    if values.get("active_plugin") not in (None, "data_analysis"):
        return []
    snap = snapshot(values, workspace)
    open_path = snap.get("report_open_path")
    return [open_path] if isinstance(open_path, str) else []


__all__ = [
    "AnalysisEvidence",
    "AnalysisState",
    "StrictModel",
    "artifact_paths",
    "merge_executions",
    "snapshot",
]
