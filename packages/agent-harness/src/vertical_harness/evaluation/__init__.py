"""独立的质量闭环模块。导入本包不启动 worker、不联网、不修改生产提示词。"""

from vertical_harness.evaluation.config import EvaluationSettings
from vertical_harness.evaluation.datasets import RegressionCase, load_cases
from vertical_harness.evaluation.feedback import (
    ReferenceAnswer,
    UserFeedback,
    promote_feedback,
    submit_feedback,
)
from vertical_harness.evaluation.offline import evaluate_offline, optimize_prompt
from vertical_harness.evaluation.online import (
    evaluate_online_once,
    run_online_worker,
    sync_feedback_dataset,
)
from vertical_harness.evaluation.targets import EvaluationTarget

__all__ = [
    "EvaluationSettings",
    "EvaluationTarget",
    "ReferenceAnswer",
    "RegressionCase",
    "UserFeedback",
    "evaluate_offline",
    "evaluate_online_once",
    "load_cases",
    "optimize_prompt",
    "promote_feedback",
    "run_online_worker",
    "submit_feedback",
    "sync_feedback_dataset",
]
