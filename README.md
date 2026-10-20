# Agent Harness

一个 Agent，直接使用网络检索、文件工具和 Python 脚本。保留模型选择、会话历史、流式输出、取消、审批恢复、上下文压缩和用量统计。

## 开发

```powershell
uv sync --all-packages --extra dev --extra desktop
Copy-Item .env.example .env
# 填写 VA_MODEL、VA_API_KEY，按需填写 EXA_API_KEY
uv run vertical-agent "检查当前工作区文件"
cd desktop
npm ci
npm run dev
```

网络检索使用 Exa，缺少密钥时返回不可用提示，不影响文件和脚本。文本搜索使用 ripgrep；桌面运行时自带 rg。文件工具支持分页读取、定位、精确替换和原子写入，编辑保留原换行与 UTF-8 BOM。

Office/PDF 使用 Microsoft MarkItDown 提取 Markdown，仅用于阅读。修改、生成或精确读取使用保存的 Python 脚本，调用 openpyxl、python-docx、python-pptx、pypdf；科学计算库继续随桌面运行时提供。扫描件 OCR 和版式保真转换未包含。

脚本必须先保存为工作区 .py 文件，再调用 execute_python_file。脚本使用当前用户权限，保留超时、取消、输出日志和环境凭据过滤。文件工具的工作区和敏感路径检查不构成 Python 沙箱。

## 数据与升级

数据库默认位于工作区同级的 .runtime 目录，桌面使用应用数据目录。仅支持当前运行格式；不迁移旧对话、工作流和审批状态。升级前清除旧会话索引与检查点；项目文件、附件和产物保留。

## 验证与打包

```powershell
uv run pytest
uv run ruff check .
uv run mypy
cd desktop
npm run typecheck
npm test
npm run build
npm run test:smoke
npm run prepare:python
```

prepare:python 创建可移动的独立 Python 运行时，包含科学计算库和 rg。已有目标目录时会拒绝覆盖。
