"""应用日志的输出边界、生命周期、并发上下文与关键调用路径回归测试。"""

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from unittest.mock import Mock

import pytest
from langchain_core.tools import tool
from pydantic import ValidationError

from vertical_harness.log.logging import HarnessLogging
from vertical_harness.models import HarnessSettings
from vertical_harness.storage import store as store_module
from vertical_harness.storage.store import StoreFactory


@tool
def scan_numeric(manuscript_text: str = "", bibliography: list[str] | None = None) -> str:
    """Scan numeric test tool."""
    logger = logging.getLogger("vertical_harness.test")
    logger.info(
        "citation_scan_completed",
        extra={
            "event_fields": {
                "status": "passed",
                "citation_count": 1,
                "bibliography_count": 1,
                "issue_count": 0,
            }
        },
    )
    return "numeric audit ok"


def settings(**overrides) -> HarnessSettings:
    return HarnessSettings(_env_file=None, model="deepseek:deepseek-flash", **overrides)


def records(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().err.splitlines()]


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_safe_output_and_exception_diagnostics(output_format, capsys):
    HarnessLogging.configure_logging(
        settings(log_level="DEBUG", log_format=output_format, api_key="secret-api-key")
    )
    logger = logging.getLogger("vertical_harness.test")
    with HarnessLogging.log_context("run-1"):
        logger.info(
            "event\n\x1b[31m",
            extra={"event_fields": {"issue_count": 2}, "api_key": "secret-api-key"},
        )
        try:
            raise ValueError("private manuscript and secret-api-key")
        except ValueError:
            logger.exception("operation_failed")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret-api-key" not in captured.err
    assert "private manuscript" not in captured.err
    assert "ValueError" in captured.err
    assert "test_safe_output_and_exception_diagnostics" in captured.err
    assert len(captured.err.splitlines()) == 2
    assert "\x1b" not in captured.err
    if output_format == "json":
        first, second = map(json.loads, captured.err.splitlines())
        assert first["run_id"] == "run-1"
        assert first["issue_count"] == 2
        assert first["timestamp"].endswith("+00:00")
        assert second["error_type"] == "ValueError"
        assert second["traceback"]


def test_reconfiguration_preserves_host_handlers_and_filters_level(capsys):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    external = logging.getLogger("third_party")
    external_state = (external.level, external.handlers[:], external.propagate)
    parent = logging.getLogger("arbitrary_plugin")
    host_handler = logging.NullHandler()
    root.addHandler(host_handler)
    HarnessLogging.configure_logging(settings(log_level="ERROR", log_format="json"))
    HarnessLogging.configure_logging(settings(log_level="WARNING", log_format="json"))
    parent.info("hidden")
    parent.warning("visible")
    logged = records(capsys)
    assert [record["event"] for record in logged] == ["visible"]
    assert host_handler in root.handlers
    assert all(handler in root.handlers for handler in original_handlers)
    assert (external.level, external.handlers[:], external.propagate) == external_state


def test_rotating_file_output_and_reconfiguration(tmp_path, capsys):
    path = tmp_path / "logs" / "harness.log"
    config = settings(log_file=path, log_format="json", log_max_bytes=250, log_backup_count=2)
    HarnessLogging.configure_logging(config)
    parent = logging.getLogger("vertical_harness")
    handler = next(
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, RotatingFileHandler)
    )
    assert handler.maxBytes == 250
    assert handler.backupCount == 2
    for _ in range(10):
        parent.info("中文事件")
    assert path.with_name("harness.log.1").is_file()
    assert len(list(path.parent.iterdir())) <= 3
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["event"] == "中文事件"
    HarnessLogging.configure_logging(config)
    assert handler.stream is None
    parent.info("after_reconfiguration")
    assert (
        len(
            [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if "after_reconfiguration" in line
            ]
        )
        == 1
    )
    assert len(records(capsys)) == 11


def test_log_context_is_nested_and_isolated_between_async_tasks(capsys):
    HarnessLogging.configure_logging(settings(log_format="json"))
    logger = logging.getLogger("vertical_harness.test")

    async def worker(run_id):
        with HarnessLogging.log_context(run_id):
            await asyncio.sleep(0)
            logger.info("worker")
            with HarnessLogging.log_context("nested"):
                logger.info("nested")
            logger.info("restored")

    async def run():
        await asyncio.gather(worker("a"), worker("b"))

    asyncio.run(run())
    with pytest.raises(RuntimeError), HarnessLogging.log_context("failed"):
        raise RuntimeError("test")
    logger.info("outside")
    logged = records(capsys)
    assert [(item["event"], item["run_id"]) for item in logged] == [
        ("worker", "a"),
        ("nested", "nested"),
        ("restored", "a"),
        ("worker", "b"),
        ("nested", "nested"),
        ("restored", "b"),
        ("outside", None),
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("log_level", "TRACE"),
        ("log_format", "yaml"),
        ("log_max_bytes", 0),
        ("log_backup_count", -1),
    ],
)
def test_invalid_logging_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        settings(**{field: value})


def test_logging_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("VA_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("VA_LOG_FORMAT", "json")
    monkeypatch.setenv("VA_LOG_FILE", str(tmp_path / "app.log"))
    config = settings()
    assert config.log_level == "DEBUG"
    assert config.log_format == "json"
    assert config.log_file == tmp_path / "app.log"


def test_plugin_defined_fields_do_not_override_reserved_fields(capsys):
    HarnessLogging.configure_logging(settings(log_format="json"))
    with HarnessLogging.log_context("real-run"):
        logging.getLogger("independent_plugin").info(
            "domain_event",
            extra={"event_fields": {"domain_metric": 7, "run_id": "fake-run", "level": "DEBUG"}},
        )
    logged = records(capsys)
    assert logged[0]["domain_metric"] == 7
    assert logged[0]["run_id"] == "real-run"
    assert logged[0]["level"] == "INFO"


def test_error_logging_keeps_exception_and_records_identity(capsys):
    HarnessLogging.configure_logging(settings(log_format="json"))
    logger = logging.getLogger("vertical_harness.test")
    try:
        raise RuntimeError("sensitive error input")
    except RuntimeError as exc:
        logger.error(
            "operation_failed",
            extra={"event_fields": {"op_id": "test_op", "error_type": type(exc).__name__}},
        )
    logged = records(capsys)
    assert logged[-1]["event"] == "operation_failed"
    assert logged[-1]["op_id"] == "test_op"
    assert logged[-1]["error_type"] == "RuntimeError"
    assert "sensitive error input" not in json.dumps(logged)


def test_store_context_exit_does_not_misreport_caller_failure(capsys):
    config = settings(log_format="json", log_level="DEBUG", store_backend="memory")
    HarnessLogging.configure_logging(config)
    with pytest.raises(RuntimeError), StoreFactory.open_store(config):
        raise RuntimeError("caller error")
    logged = records(capsys)
    assert [item["event"] for item in logged] == [
        "store_opening",
        "store_ready",
        "store_context_exited",
    ]
    assert all(item["level"] != "ERROR" for item in logged)


def test_store_setup_failure_releases_connection(monkeypatch):
    import sqlite3

    connection = Mock()
    failure = RuntimeError("setup failed")
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connection)
    monkeypatch.setattr(store_module.SqliteStore, "setup", Mock(side_effect=failure))
    with (
        pytest.raises(RuntimeError) as exc,
        store_module.StoreFactory.open_store(settings(store_db_path=":memory:")),
    ):
        pytest.fail("Store setup must fail before exposing the connection")
    assert exc.value is failure
    connection.close.assert_called_once_with()
