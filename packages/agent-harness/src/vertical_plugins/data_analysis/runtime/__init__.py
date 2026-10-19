"""科研数据分析运行时子包公共导出：执行凭证、检查点节点控制 Hook 与桌面端状态投影。"""

from vertical_plugins.data_analysis.runtime.desktop import DESCRIPTOR, artifact_paths, snapshot
from vertical_plugins.data_analysis.runtime.evidence import (
    AnalysisEvidence,
    AnalysisState,
    merge_executions,
)
from vertical_plugins.data_analysis.runtime.hooks import (
    fork_node_branch,
    inspect_node_checkpoints,
    modify_node_state,
    retry_node,
)

__all__ = [
    "DESCRIPTOR",
    "AnalysisEvidence",
    "AnalysisState",
    "artifact_paths",
    "fork_node_branch",
    "inspect_node_checkpoints",
    "merge_executions",
    "modify_node_state",
    "retry_node",
    "snapshot",
]
