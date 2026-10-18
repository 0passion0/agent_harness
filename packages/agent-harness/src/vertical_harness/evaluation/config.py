"""评测配置独立于在线智能体配置；所有模型复用宿主的 VA_* 连接。"""

import logging
from typing import Annotated

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vertical_harness.config.settings import NonEmptyStr

logger = logging.getLogger(__name__)


class EvaluationSettings(BaseSettings):
    """评测环境与参数专用配置模型，从 VA_ 环境变量加载。"""

    model_config = SettingsConfigDict(env_prefix="VA_", env_file=".env", extra="ignore")

    # 用于 LLM-as-a-Judge 评测的模型名称（None 时默认回退到主模型）
    eval_model: NonEmptyStr | None = None
    # 用于提示词自动化优化的模型名称（None 时默认回退到主模型）
    optimizer_model: NonEmptyStr | None = None
    # 离线评测并发执行数（1-32）
    eval_concurrency: Annotated[int, Field(ge=1, le=32)] = 2
    # 是否启用在线抽样评测后台 Worker
    enable_online_evaluation: bool = False
    # 在线抽样评测采样率（0.0 - 1.0）
    online_eval_sample_rate: Annotated[float, Field(ge=0, le=1)] = 0.1
    # 在线评测轮询间隔秒数（>= 10）
    online_eval_poll_seconds: Annotated[int, Field(ge=10)] = 60
    # 在线评测回溯时间窗口小时数（1 - 8760）
    online_eval_lookback_hours: Annotated[int, Field(ge=1, le=8760)] = 24
    # 是否启用用户反馈自动沉淀为回归测试集
    enable_feedback_regression: bool = False
    # 回归测试集目标名称（当 enable_feedback_regression=True 时必填）
    regression_dataset: str | None = None

    @model_validator(mode="after")
    def validate_regression_dataset(self) -> "EvaluationSettings":
        """校验启用用户反馈回归测试集时必须指定回归测试集名称。

        :return: 校验合法的当前 EvaluationSettings 实例。
        :raises ValueError: 当启用回归测试集但未指定数据集名称时抛出。
        """
        logger.debug(
            "validate_regression_dataset_started",
            extra={
                "event_fields": {
                    "enable_feedback_regression": self.enable_feedback_regression,
                    "has_regression_dataset": bool(self.regression_dataset),
                }
            },
        )
        if self.enable_feedback_regression and not self.regression_dataset:
            raise ValueError(
                "VA_REGRESSION_DATASET is required when feedback regression is enabled"
            )
        return self
