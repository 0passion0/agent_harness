"""Optional native LangChain model router driven by trusted runtime context."""

from collections.abc import Callable

from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from langchain.chat_models import init_chat_model

from vertical_harness.settings import HarnessSettings


def make_router(settings: HarnessSettings):
    """Provide an opt-in route rather than copying the model call loop.

    :param settings: Model identifiers and routing configuration.
    :return: LangChain model-call middleware or None.
    :raises ValueError: When only one routing model is configured.
    """
    if not settings.economy_model and not settings.quality_model:
        return None
    if not settings.economy_model or not settings.quality_model:
        raise ValueError("Both VA_ECONOMY_MODEL and VA_QUALITY_MODEL must be set")

    economy = init_chat_model(settings.economy_model)
    quality = init_chat_model(settings.quality_model)

    @wrap_model_call
    def route_model(
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Select model using validated, trusted per-run task tier."""
        task_tier = getattr(request.runtime.context, "task_tier", "quality")
        model = economy if task_tier == "economy" else quality
        return handler(request.override(model=model))

    return route_model
