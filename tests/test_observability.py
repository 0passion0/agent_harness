"""Tracing 使用 .env 配置，同时尊重离线评测的关闭上下文。"""

from unittest.mock import Mock

from langsmith import tracing_context
from langsmith.run_helpers import get_tracing_context

from vertical_harness.log.observability import LangSmithSettings, tracing_session


def test_dotenv_configuration_and_tracing_lifecycle(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING")
    path = tmp_path / ".env"
    path.write_text(
        "LANGSMITH_TRACING=true\nLANGSMITH_API_KEY=private-key\nLANGSMITH_PROJECT=testing\nVA_MODEL=unused\n"
    )
    settings = LangSmithSettings(_env_file=path)
    assert settings.tracing and settings.project == "testing"
    assert settings.api_key.get_secret_value() == "private-key"
    client = Mock()
    monkeypatch.setattr(LangSmithSettings, "create_client", lambda self: client)
    with tracing_session(settings):
        state = get_tracing_context()
        assert state["enabled"] is True
        assert state["client"] is client
        assert state["project_name"] == "testing"
    client.flush.assert_called_once()
    client.close.assert_called_once()


def test_disabled_or_outer_local_context_never_creates_client(monkeypatch):
    factory = Mock(side_effect=AssertionError("No network client expected"))
    monkeypatch.setattr(LangSmithSettings, "create_client", factory)
    with tracing_session(LangSmithSettings(_env_file=None, tracing=False)):
        assert get_tracing_context()["enabled"] is False
    with (
        tracing_context(enabled=False),
        tracing_session(LangSmithSettings(_env_file=None, tracing=True)),
    ):
        assert get_tracing_context()["enabled"] is False
    factory.assert_not_called()


def test_non_upload_client_does_not_start_a_network_worker(monkeypatch):
    import requests

    request = Mock(side_effect=AssertionError("No request expected"))
    monkeypatch.setattr(requests.Session, "request", request)
    config = LangSmithSettings(_env_file=None, api_key="test")
    client = config.create_client(auto_batch_tracing=False)
    try:
        assert client.tracing_queue is None
    finally:
        client.close()
    request.assert_not_called()
