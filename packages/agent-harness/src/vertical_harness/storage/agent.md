# 存储模块 (vertical_harness/storage) - Agent 指南与规范

## 目录定位与职责
`vertical_harness/storage/` 负责为智能体运行时提供状态持久化支持（Checkpointers）、跨会话键值数据存储（BaseStore）以及物理/逻辑多租户隔离。

## 核心组件与文件
- `backends.py`：存储后端工厂与生命周期管理：
  - `open_store()` / `aopen_store()`：创建并管理支持内存、SQLite 或 Postgres 的 LangGraph BaseStore 上下文。
  - `open_checkpointer()` / `aopen_checkpointer()`：创建并管理支持内存、SQLite 或 Postgres 的检查点持久化上下文。
  - `compose_backend()`：构建 Deep Agents 所需的文件系统与技能存储后端协议实现。
- `namespace.py`：多租户命名空间隔离工具：
  - 规范化租户 ID、用户 ID 与业务域生成不可碰撞的 Tuple 格式命名空间键路径。

## 关键约束与安全规范
1. **租户绝对隔离**：所有跨会话存储读写必须经由命名空间前缀限制，禁止无租户边界的全局键遍历。
2. **连接池与文件释放**：存储后端涉及的数据库连接与文件锁必须通过上下文管理器（Context Manager）进行严格释放。
3. **备份与灾难恢复**：生产环境下优先推荐带有持久化与并发控制的 Postgres 后端。
