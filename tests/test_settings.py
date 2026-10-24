import pytest
from pydantic import ValidationError

from vertical_harness.models import HarnessSettings


def test_context_budget_accepts_environment_integer(monkeypatch):
    monkeypatch.setenv("VA_MAX_CONTEXT_TOKENS", "128000")
    settings = HarnessSettings(_env_file=None, model="test:fake")
    assert settings.max_context_tokens == 128_000


@pytest.mark.parametrize("budget", [True, 1.5, 0, 475_001, "1.5", "invalid"])
def test_context_budget_rejects_invalid_values(budget):
    with pytest.raises(ValidationError):
        HarnessSettings(_env_file=None, model="test:fake", max_context_tokens=budget)
