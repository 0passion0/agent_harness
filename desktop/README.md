# MedPeer 桌面前端

桌面工作台的 Python JSON-RPC 后端作为 `vertical_harness.desktop` 随核心包安装；Electron 主进程使用仓库 `.venv` 启动该模块。页面保留现有风格，聊天内展示工作流、节点和工具状态；桌面专用运行配置和附件处理位于核心包的 `vertical_harness.desktop_runtime` 与 `vertical_harness.desktop_attachments`。

## 启动

先按仓库根目录说明安装 `.venv` 及工作区 Python 依赖。桌面会枚举所有已安装插件，因此需要 `analysis` extra；附件解析依赖由 `desktop` extra 提供：

```powershell
uv sync --frozen --all-packages --extra dev --extra analysis --extra desktop
```

仓库根目录 `.env` 使用当前后端的 `VA_MODEL`、`VA_API_KEY` 和 `VA_BASE_URL`。密钥只由 Python 主进程读取，不写入前端代码。设置页可保存模型名称并实际测试连接。

Windows 双击根目录 `start-desktop.bat`，或在此目录执行：

```powershell
npm ci
npm run dev
```

需要 Node.js/npm。启动脚本使用当前仓库 `.venv\Scripts\python.exe`，没有安装包和系统 Python 回退。

## 登录和数据

使用迁入的 MedPeer SSO 登录窗口。SSO 地址来自 `settings.example.json`；如需部署配置，复制为 `settings.json` 并通过环境变量 `MEDPEER_CONFIG_FILE` 指向它的绝对路径。服务支持 HTTPS 时可在配置中使用 HTTPS。默认地址保持源项目配置。

凭据由 Electron 主进程调用 Windows 系统加密后保存到 `%APPDATA%\agent-harness-desktop\credentials.bin`，不会发送给渲染进程。每次重启向 SSO 验证 Cookie。退出登录删除凭据，保留该账号的本地会话，重新登录同一账号可继续使用。

项目索引、对话 Checkpoint、记忆库和默认工作目录位于 `%APPDATA%\agent-harness-desktop\accounts\<账号哈希>\`，每个项目独立存储。可以关联已有文件夹。新建项目后可发送消息和附件、流式接收、停止、批准或拒绝工具操作；侧栏支持搜索、重命名、恢复、导出和删除会话。

附件支持原版的导入、拖放、预览与打开；CSV、TSV、XLSX、PDF、DOCX 和文本提取后进入模型上下文，文本预览总预算为 20,000 字符。图片按原版多模态消息传递，是否接受由所选模型决定。项目文件的复制不会覆盖同名文件。

全局/项目指令、Skills、MCP 配置与本地用量统计均有独立持久化。用量来自模型返回的 token 元数据，未定价的费用不作虚构；上下文窗口显示的是估算值。

后端通过已安装的插件 entry points 动态发现插件、工作流和 Skills。输入框按 `/` 后分别选择插件或技能；主动选择插件授权其在本轮使用，主智能体自行决定调用的插件需先弹窗确认。技能选择不授权插件执行；禁用插件的技能不可选。授权按用户、项目、会话和本轮运行约束，审批范围扩大时再次确认。旧客户端历史、凭据和 CLI Checkpoint 不自动迁移；MedPeer 账户余额由 SSO 提供，不在本机扣费。

## 检查

本次实际执行结果、真实模型验收范围与未验证项见 [TESTING.md](TESTING.md)。

```powershell
npm run typecheck
npm test
npm run build
npm run test:smoke
..\.venv\Scripts\python.exe -X utf8 -m pytest ..\tests\desktop -q -o addopts=''
```

真实模型集成测试会调用已配置的 DeepSeek 接口：

```powershell
$env:DESKTOP_LIVE_TEST='1'
npx vitest run tests/live-runtime.test.ts
```

Electron 冒烟测试使用真实窗口和预加载接口，后端聊天响应使用测试数据；真实模型流程由上面的独立集成测试验证。SSO 需要真实用户交互登录，未将自动化测试数据当作真实登录成功。
