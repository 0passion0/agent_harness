"""评测配置独立于在线智能体配置；所有模型复用宿主的 VA_* 连接。"""

from typing import Annotated

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vertical_harness.config.settings import NonEmptyStr


class EvaluationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VA_", env_file=".env", extra="ignore")

    eval_model: NonEmptyStr | None = None
    optimizer_model: NonEmptyStr | None = None
    eval_concurrency: Annotated[int, Field(ge=1, le=32)] = 2
    enable_online_evaluation: bool = False
    online_eval_sample_rate: Annotated[float, Field(ge=0, le=1)] = 0.1
    online_eval_poll_seconds: Annotated[int, Field(ge=10)] = 60
    online_eval_lookback_hours: Annotated[int, Field(ge=1, le=8760)] = 24
    enable_feedback_regression: bool = False
    regression_dataset: str | None = None

    @model_validator(mode="after")
    def validate_regression_dataset(self) -> "EvaluationSettings":
        if self.enable_feedback_regression and not self.regression_dataset:
            raise ValueError(
                "VA_REGRESSION_DATASET is required when feedback regression is enabled"
            )
        return self
