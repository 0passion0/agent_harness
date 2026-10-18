"""原生 LangGraph SQLite Store 与 Deep Agents CompositeBackend 解耦架构测试模块。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from vertical_harness import (
    HarnessSettings,
    RunContext,
    SqliteStore,
    aopen_store,
    compose_backend,
    open_store,
    tenant_memory_namespace,
)


def test_native_sqlite_store_lifecycle_and_crud(tmp_path: Path) -> None:
    """测试 open_store 委托给官方 SqliteStore 并支持跨会话持久化 CRUD 操作。

    :param tmp_path: pytest 提供的临时测试目录路径对象。
    :return: None
    :raises AssertionError: 当 SQLite 存储实例类型、键值存取或物理文件存在性验证失败时触发。
    """
    # 构造测试配置，将工作区指向 pytest 临时目录
    settings = HarnessSettings(
        _env_file=None,
        model="deepseek:deepseek-flash",
        workspace_root=tmp_path / "ws",
    )
    # 模拟多租户三元组命名空间：(租户 ID, 用户 ID, 存储桶)
    ns = ("tenant_alpha", "user_1", "memories")

    # 会话 1：使用 open_store 上下文写入数据并退出（触发连接关闭）
    with open_store(settings) as store:
        assert isinstance(store, SqliteStore)
        # 写入用户偏好键值数据
        store.put(ns, "pref", {"theme": "dark", "citations": "ieee"})

    # 会话 2：重新打开存储，验证数据已持久化并执行读取与删除操作
    with open_store(settings) as store:
        reloaded = store.get(ns, "pref")
        # 验证读取到的数据值与先前写入完全一致
        assert reloaded is not None
        assert reloaded.value == {"theme": "dark", "citations": "ieee"}
        # 删除该键值项
        store.delete(ns, "pref")
        # 验证删除生效，再次读取返回 None
        assert store.get(ns, "pref") is None

    # 验证磁盘上物理 SQLite 文件确实生成在工作区根目录下
    assert (tmp_path / "ws" / "store.sqlite").exists()


def test_native_async_sqlite_store_lifecycle(tmp_path: Path) -> None:
    """测试 aopen_store 异步上下文管理器委托给官方 AsyncSqliteStore 的生命周期与数据读写。

    :param tmp_path: pytest 提供的临时测试目录路径对象。
    :return: None
    :raises AssertionError: 当异步存取操作得到的数据与预期不一致时触发。
    """
    # 构造测试配置，显式指定异步存储数据库路径
    settings = HarnessSettings(
        _env_file=None,
        model="deepseek:deepseek-flash",
        store_db_path=tmp_path / "async_store.sqlite",
    )

    async def _run() -> None:
        """异步执行协程：验证 AsyncSqliteStore 的 aput 与 aget 操作。

        :return: None
        """
        # 进入异步上下文管理器打开 AsyncSqliteStore
        async with aopen_store(settings) as store:
            ns = ("async_tenant", "user_42", "memories")
            # 异步写入键值对
            await store.aput(ns, "k1", {"fact": "evidence-first"})
            # 异步读取验证
            item = await store.aget(ns, "k1")
            assert item is not None
            assert item.value == {"fact": "evidence-first"}

    # 通过 asyncio 事件循环运行协程
    asyncio.run(_run())


def test_composite_backend_routes_memories_to_sqlite_store(tmp_path: Path) -> None:
    """测试 CompositeBackend 将 /memories/ 路径路由至 SqliteStore，同时将工作区文件保存在磁盘上。

    :param tmp_path: pytest 提供的临时测试目录路径对象。
    :return: None
    :raises AssertionError: 当文件写入错误、磁盘存储隔离状态或带特殊字符的命名空间解析断言失败时触发。
    """
    # 创建物理工作区目录
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = HarnessSettings(
        _env_file=None,
        model="deepseek:deepseek-flash",
        workspace_root=workspace,
    )
    # 模拟包含特殊字符 '.' 的租户与用户运行时上下文对象（如 'lab.a', 'researcher.7'）
    fake_runtime = SimpleNamespace(
        context=RunContext(tenant_id="lab.a", user_id="researcher.7", task_tier="quality")
    )

    # 打开持久化存储并组装复合后端
    with open_store(settings) as store:
        backend = compose_backend(
            workspace,
            store,
            memory_route="/memories/",
            namespace_factory=lambda _rt: tenant_memory_namespace(fake_runtime),
        )

        # 写入普通工作区草稿文件与记忆路由文件
        ws_write = backend.write("/draft.txt", "Manuscript draft [1]")
        mem_write = backend.write("/memories/AGENTS.md", "# Lab Preferences\nAlways check DOI.")
        # 验证写入均未发生异常
        assert ws_write.error is None
        assert mem_write.error is None

        # 核心隔离验证：普通文件直接持久化在磁盘工作区中
        assert (workspace / "draft.txt").exists()
        # 长期记忆文件通过路由重定向到 SQLite Store，绝不会在物理磁盘工作区创建文件夹或文件
        assert not (workspace / "memories" / "AGENTS.md").exists()

        # 验证命名空间字符清洗机制：租户与用户 ID 中的 '.' 被自动清洗为 '_'，防止与 SqliteStore 分隔符冲突
        item = store.get(("lab_a", "researcher_7", "memories"), "/AGENTS.md")
        assert item is not None
        assert "Always check DOI." in item.value["content"]

        # 验证通过虚拟后端能够透明读取之前写入的记忆内容
        read_mem = backend.read("/memories/AGENTS.md")
        assert read_mem.error is None
        assert read_mem.file_data is not None
        assert "Always check DOI." in read_mem.file_data["content"]

