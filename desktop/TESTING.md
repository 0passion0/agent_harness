# 桌面迁移验收记录

验证日期：2026-10-19，Windows，本项目 `.venv`、Electron 44、已配置的 DeepSeek。

## 界面一致性

对比源目录 `D:\project\medpeer_agent_cli\desktop` 的当前文件，而非旧提交。`check-renderer-parity.mjs` 检查渲染层、预加载接口和共享协议：37 个文件通过；文本统一换行后比较哈希，排除说明文件 `agent.md`。

仅声明三处非视觉适配：模型 provider 类型、停止/回复事件的竞态状态、交互回复携带 interaction_id。布局、样式、文案和图标使用源项目文件。

Electron 实际窗口冒烟测试通过：原版登录页、主布局、预加载接口、发送与流式回复、8 个设置页、3 个详情页、主题切换、新建任务。聊天和登录后用户资料使用测试数据，不能据此认定真实 SSO 已登录。

启动 IPC 回归：开发服务器提供的 URL 没有末尾 `/`，Electron 的 frame URL 会补齐，原来的字符串比较因此拒绝正常调用。修复前已复现 `Untrusted IPC sender`；现在对可信加载 URL 先使用标准 URL 序列化，仍保留窗口、主 frame 和完整 URL 校验。新增 HTTP 开发地址下的真实 `app.snapshot` 调用，文件加载模式仍执行完整界面冒烟测试，两种模式均通过。HTTP 检查使用 Vite 提供构建页面，未重复执行完整的 HMR 流程。

截图位于 `test-artifacts/login.png` 和 `test-artifacts/workspace-dark.png`。

## 执行结果

| 检查 | 结果 | 证明范围 |
| --- | --- | --- |
| `npm run typecheck` | 通过 | TypeScript 接口与调用 |
| `npm test` | 62 通过，1 跳过 | 原版组件、状态竞态、主进程命令、项目/账号隔离、会话、配置、附件与信任边界；默认跳过真实模型测试 |
| `.venv\Scripts\python.exe -X utf8 -m pytest desktop/tests -q -o addopts=''`（仓库根目录） | 21 通过 | Bridge、实际工具图、权限、持久中断、上下文限制、附件提取、MCP 发现与调用 |
| Ruff（本次桌面 Python 文件和测试） | 通过 | 静态检查 |
| `npm run test:smoke` | 通过 | 生产构建及 Electron 窗口操作 |
| `node scripts/check-renderer-parity.mjs D:\project\medpeer_agent_cli\desktop` | 通过 | 上述 37 文件一致性 |
| `git diff --check` | 通过 | 无空白错误；Git 提示 Windows 换行转换 |
| `uv pip check` | 通过，156 个包 | 已安装 Python 依赖一致性 |

除表中特别注明外，npm 和 node 命令在 `desktop` 目录执行。

## 真实 DeepSeek 集成

运行 `$env:DESKTOP_LIVE_TEST='1'; npx vitest run tests/live-runtime.test.ts`：1 项集成测试通过，用时约 36 秒。使用真实 Python Bridge、DeepSeek HTTP 接口、工作区文件及 SQLite Checkpoint；账号资料使用本地测试对象。

依次验证了：

1. 模型连接测试与中文请求成功。
2. 导入 CSV，读取 2、4、6，实际调用 write_file 生成 `sum=12`。
3. 工具卡片事件具有实际参数、输出和耗时。
4. 模型 token 用量、上下文估算和会话导出有真实记录。
5. 关闭并重新启动 Bridge 后，会话消息、附件和产物一致恢复。
6. ask 权限下写文件前出现审批；批准后才创建文件。
7. 拒绝后不创建文件，模型正常完成回复。
8. 停止待审批任务后不创建文件，且可继续新一轮对话。
9. 立即停止运行中的任务，返回 cancelled。

执行日志和事件保存在已忽略的 `test-artifacts/live-runtime.log`、`test-artifacts/live-events.json`，不提交凭据。

## 范围和未验证项

- 按用户要求，科研插件和 Exa 后端接入不在本次范围内；原版页面及本地配置保留，运行状态不伪装为已加载。
- 未用真实用户完成 MedPeer SSO 人工登录。自动化覆盖回调校验、账号一致性、凭据恢复、网络失败与失效 Cookie；Windows 凭据加密使用 Electron safeStorage，单元测试中为替身。
- 图片按原版多模态协议传递，未验证当前模型的图片理解；PDF 测试覆盖解析及空白文档，未覆盖扫描件 OCR。
- 第三方 MCP 服务器未逐个连接；已用真实本地 stdio MCP 服务验证发现与工具调用。
- 实际集成日志出现底层 LangGraph 异步 Store 任务释放警告 `Task was destroyed but it is pending!`；本次会话恢复和取消测试通过。现有后端包不属于本次修改范围，未用私有 API 绕过该问题。
- 工作区 `packages/` 中有其他来源的并发修改；本次桌面迁移未编辑或回退这些文件。
