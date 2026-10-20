# MedPeer 桌面前端

桌面工作台的 Python JSON-RPC 后端作为 `vertical_harness.desktop` 随核心包安装；开发时 Electron 主进程使用仓库 `.venv` 启动该模块，发布包使用随包运行时。页面保留现有风格，聊天内展示工作流、节点和工具状态；桌面专用运行配置和附件处理位于核心包的 `vertical_harness.desktop_runtime` 与 `vertical_harness.desktop_attachments`。

## 启动

先按仓库根目录说明安装 `.venv` 及工作区 Python 依赖。桌面会枚举所有已安装插件，因此需要 `analysis` extra；附件解析依赖由 `desktop` extra 提供：

```powershell
uv sync --frozen --all-packages --extra dev --extra analysis --extra desktop
```

发布包中的 Python 运行时优先于设置页填写的解释器，用于运行桌面后端和用户保存的分析脚本。准备 Windows 发布运行时需要已安装 `uv`，执行：

```powershell
npm run prepare:python
```

脚本使用 `uv.lock` 中 `vertical-agent-harness` 的锁定依赖，并将其 wheel 与 `analysis`、`desktop` extras 安装到 Python 3.12 standalone runtime，验证后放入 `resources/python`；Electron 打包配置会将其复制到应用资源目录。该脚本只准备运行时，仓库当前没有 Electron 安装包构建命令。运行 `prepare:python` 前需确保目标目录不存在。开发时桌面后端继续使用仓库 `.venv`；未打包运行时，保存脚本可回退到设置页中的 Python 路径。

仓库根目录 `.env` 使用当前后端的 `VA_MODEL`、`VA_API_KEY` 和 `VA_BASE_URL`。密钥只由 Python 主进程读取，不写入前端代码。设置页可保存模型名称并实际测试连接。

Windows 双击根目录 `start-desktop.bat`，或在此目录执行：

```powershell
npm ci
npm run dev
```

需要 Node.js/npm。开发启动脚本使用当前仓库 `.venv\Scripts\python.exe`，没有系统 Python 回退。

## 本地数据与项目存储

桌面端采用纯本地存储架构，无需登录或账号认证，启动即可直接使用。

数据根目录默认位于 %APPDATA%\agent-harness-desktop\：
- 项目索引与元数据保存在 index.json 中；
- 各项目的对话 Checkpoint、记忆库与默认工作目录独立保存在 projects/<项目ID>/ 目录下；
- 机器全局偏好（主题设置等）、授权审批记录与本地 Token 用量统计保存在 global/ 目录下。

支持关联已有本地文件夹作为项目工作目录。新建项目后可发送消息和附件、流式接收、停止、批准或拒绝工具操作；侧栏支持搜索、重命名、恢复、导出和删除会话。

附件支持导入、拖放、预览与打开；CSV、TSV、XLSX、PDF、DOCX 和文本提取后进入模型上下文，文本预览总预算为 20,000 字符。图片按多模态消息传递，是否接受由所选模型决定。项目文件的复制不会覆盖同名文件。

全局/项目指令、Skills、MCP 配置与本地用量统计均有独立持久化。用量来自模型返回的 token 元数据，未定价的费用不作虚构；上下文窗口显示的是估算值。

后端通过已安装的插件 entry points 动态发现插件、工作流和 Skills。输入框按 / 后分别选择插件或技能；主动选择插件授权其在本轮使用，主智能体自行决定调用的插件需先弹窗确认。技能选择不授权插件执行；禁用插件的技能不可选。授权按项目、会话和本轮运行约束，审批范围扩大时再次确认。

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

Electron 冒烟测试使用真实窗口和预加载接口，后端聊天响应使用测试数据；真实模型流程由上面的独立集成测试验证。
