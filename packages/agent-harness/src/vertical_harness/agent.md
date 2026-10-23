# Python 后端指南

遵循仓库 docs/BACKEND_CODING_STANDARD.md 和 docs/BACKEND_ARCHITECTURE.md。

- 类接口、每文件一个类；不定义模块函数、嵌套函数、旧路径映射或方法别名。
- core/compose 装配原生 Deep Agents；不自研循环或工作流引擎。core/context 是可信身份契约。
- config 管配置，execution 管进程，filesystem 管文件，storage 管检查点和 Store。
- middleware 适配框架策略；runtime 管资源和运行；bridge/desktop/cli 处理宿主输入输出。
- 保留权限、审批、预算、取消、BOM/换行和原子替换；日志不写用户正文或密钥。
- 重构不得删除用户数据、改变检查点键或中间件身份。Python/Shell 不是安全沙箱。
- 使用本地 .venv 运行 pytest、ruff check .、mypy；公开方法完整注解，全包检查。
