
"""原生 LangGraph SQLite Store 与 Deep Agents CompositeBackend 解耦架构测试模块。"""
import asyncio
from pathlib import Path

from langgraph.store.sqlite import SqliteStore

from vertical_harness import HarnessSettings, aopen_store, open_store


def test_native_sqlite_store_lifecycle_and_crud(tmp_path: Path) -> None:
    """测试 open_store 委托给官方 SqliteStore 并支持跨会话持久化 CRUD 操作。

    :param tmp_path: pytest 提供的临时测试目录路径对象。
    :return: None
    :raises AssertionError: 当 SQLite 存储实例类型、键值存取或物理文件存在性验证失败时触发。
    """
    settings = HarnessSettings(
        _env_file=None, model="deepseek:deepseek-flash", workspace_root=tmp_path / "ws"
    )
    ns = ("tenant_alpha", "user_1", "memories")
    with open_store(settings) as store:
        assert isinstance(store, SqliteStore)
        store.put(ns, "pref", {"theme": "dark", "citations": "ieee"})
    with open_store(settings) as store:
        reloaded = store.get(ns, "pref")
        assert reloaded is not None
        assert reloaded.value == {"theme": "dark", "citations": "ieee"}
        store.delete(ns, "pref")
        assert store.get(ns, "pref") is None
    assert settings.resolved_store_target.exists()


def test_native_async_sqlite_store_lifecycle(tmp_path: Path) -> None:
    """测试 aopen_store 异步上下文管理器委托给官方 AsyncSqliteStore 的生命周期与数据读写。

    :param tmp_path: pytest 提供的临时测试目录路径对象。
    :return: None
    :raises AssertionError: 当异步存取操作得到的数据与预期不一致时触发。
    """
    settings = HarnessSettings(
        _env_file=None,
        model="deepseek:deepseek-flash",
        store_db_path=tmp_path / "async_store.sqlite",
    )

    async def _run() -> None:
        """异步执行协程：验证 AsyncSqliteStore 的 aput 与 aget 操作。

        :return: None
        """
        async with aopen_store(settings) as store:
            ns = ("async_tenant", "user_42", "memories")
            await store.aput(ns, "k1", {"fact": "evidence-first"})
            item = await store.aget(ns, "k1")
            assert item is not None
            assert item.value == {"fact": "evidence-first"}

    asyncio.run(_run())
