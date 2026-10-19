/**
 * @file protocol.ts
 * @description 前后端交互协议与核心数据类型契约定义。
 * 覆盖前后端 JSON-RPC 命令、事件帧、会话快照、用量统计及 DesktopAPI 原生桥接接口。
 * @author tongliga
 * @date 2026-10-16
 */

/**
 * 兼容标准 JSON 格式的基础数据类型联合。
 */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }

/**
 * 模型调用费用及研值估算区间。
 * 包含峰谷电价/单价浮动区间以及关联费率日期。
 */
export type CostEstimate = {
  /** 预计消耗最低金额（人民币元，字符串形式保留两位小数） */
  minimum_cny: string
  /** 预计消耗最高金额（人民币元，字符串形式保留两位小数） */
  maximum_cny: string
  /** 预计消耗最低研值数（20元 = 2000研值） */
  minimum_credits: string
  /** 预计消耗最高研值数 */
  maximum_credits: string
  /** 当前估算依据的单价快照生效日期列表 */
  rate_dates: string[]
}

/**
 * 单次会话内特定模型的 Token 消耗与请求频次统计。
 */
export type SessionModelUsage = {
  /** 模型标识代码（如 agnes-3.0-flash） */
  model: string
  /** 发起调用的总请求次数 */
  requests: number
  /** 输入（提示词）所消耗的 Token 数量 */
  input_tokens: number
  /** 输出（生成文本）所消耗的 Token 数量 */
  output_tokens: number
  /** 总计消耗的 Token 数量（输入 + 输出） */
  total_tokens: number
  /** 命中上下文缓存的 Token 数量，为 null 表示后端未返回缓存统计 */
  cache_hit_tokens: number | null
  /** 基于此模型调用的费用与研值估算，未计价时为 null */
  cost_estimate: CostEstimate | null
  /** 未能成功匹配单价的请求次数 */
  unpriced_requests: number
  /** 属于免研值计费的 JEV 系列请求次数 */
  jev_requests: number
}

/**
 * 汇总级别的用量统计结构。
 */
export type UsageSummary = {
  /** 累计已发起的请求总次数 */
  requests: number
  /** 成功上报了用量数据的请求次数 */
  reported_requests: number
  /** 输入消耗的 Token 总量 */
  input_tokens: number
  /** 输出消耗的 Token 总量 */
  output_tokens: number
  /** 累计消耗的 Token 总量 */
  total_tokens: number
  /** 缓存读取的 Token 数量（为输入 Token 的子集） */
  cache_read: number | null
  /** 深度思考或推理消耗的 Token 数量（为已报告 Token 的子集） */
  reasoning: number | null
  /** 费用与研值估算结果 */
  cost_estimate: CostEstimate | null
  /** 未计价请求次数 */
  unpriced_requests: number
  /** JEV 免计费请求次数 */
  jev_requests: number
}

/**
 * 单个模型的完整用量模型，包含来源分类。
 */
export type UsageModel = UsageSummary & {
  /** 模型名称 */
  model: string
  /** 模型来源类别：标准模型 ('model') 或 JEV 平台 ('jev') */
  source: 'model' | 'jev'
}

/**
 * 全局或指定周期的用量报表完整数据结构。
 */
export type UsageStats = {
  /** 统计时区标识（如 Asia/Shanghai） */
  timezone: string
  /** 当前统计当天日期（YYYY-MM-DD） */
  today: string
  /** 本机数据最早记录的其实时间（可能为 null） */
  tracking_since: string | null
  /** 统计覆盖的日期列表 */
  dates: string[]
  /** 出现过的所有模型名称列表 */
  model_names: string[]
  /** 所有模型的累计汇总数据 */
  totals: UsageSummary
  /** 按模型维度聚合的统计明细 */
  models: UsageModel[]
  /** 按天与模型双维度拆分的每日统计记录 */
  daily: Array<UsageModel & { date: string }>
}

/**
 * 会话上下文某一特定区块（如系统提示词、记忆、工具）的 Token 占用详情。
 */
export type TokenSectionUsage = {
  available?: boolean
  /** 区块名称（如 '系统提示词'、'技能 (Skills)'） */
  name: string
  /** 当前区块所占用的 Token 数量 */
  tokens: number | null
  /** 当前区块对应的 UTF-8 字节大小 */
  bytes: number | null
  /** 在当前已使用总 Token 中的占比（0.0 ~ 1.0） */
  ratio_of_used: number | null
  /** 在模型最大允许上下文 Token 窗口中的占比（0.0 ~ 1.0） */
  ratio_of_max: number | null
  /** 区块功能或构成的简要描述 */
  description: string
}

/**
 * 完整的会话上下文 Token 占用分析报告。
 */
export type SessionTokenReport = {
  /** 模型最大上下文窗口容量（默认 128,000） */
  max_tokens: number
  /** 当前上下文已占用的 Token 总数 */
  used_tokens: number
  /** 上下文剩余可用 Token 总数 */
  remaining_tokens: number
  /** 已使用 Token 占最大窗口的比例（0.0 ~ 1.0） */
  used_ratio: number
  /** 剩余 Token 占最大窗口的比例（0.0 ~ 1.0） */
  remaining_ratio: number
  /** 各上下文组成部分的细分统计 */
  sections: {
    system_prompt: TokenSectionUsage
    skills: TokenSectionUsage
    mcp: TokenSectionUsage
    memory: TokenSectionUsage
    history: TokenSectionUsage
    [key: string]: TokenSectionUsage
  }
  /** 适合在终端或状态栏直接展示的单行摘要字符串 */
  summary_text: string
}

/**
 * 单个任务会话的历史快照。
 */
export type Snapshot = {
  /** 会话全局唯一 ID */
  session_id: string
  /** 当前活跃的线程 ID */
  thread_id: string
  /** 该会话关联的所有子线程 ID */
  thread_ids: string[]
  /** 历史对话消息序列（包含人类提示词与 AI 响应） */
  messages: Array<{ type: string; data: { content: Json; [key: string]: Json } }>
  /** 该会话关联的用户上传附件列表 */
  attachments: AttachmentRef[]
  /** 该会话中生成的交付物与成果文件列表 */
  artifacts: ArtifactRef[]
  /** 每一轮运行的历史执行详情记录 */
  runs: Array<{ requests: Json[]; tools: Record<string, Json>; elapsed: number; id?: string; started_at?: string; finished_at?: string; status?: 'complete' | 'failed' | 'cancelled'; records?: RunRecord[]; user_message_id?: string }>
  active_run?: { id: string; started_at: string; records: RunRecord[]; user_message_id?: string }
  /** 本会话总体的 Token 用量与估算数据 */
  totals: Record<string, Json>
  /** 是否有尚未归档沉淀的历史增量 */
  unsettled_history: boolean
  /** 当前上下文的实时 Token 占用报告 */
  token_usage?: SessionTokenReport
}

/**
 * 插件基础信息描述契约。
 */
export type PluginDescriptor = {
  /** 插件唯一英文标识符（如 data_analysis） */
  id: string
  /** 插件用户界面显示名称 */
  name: string
  /** 插件功能描述 */
  description: string
  /** 支持的人机交互交互形态类别列表 */
  interaction_kinds: string[]
}

/**
 * 运行时插件状态（扩展自插件描述符）。
 */
export type AppPlugin = PluginDescriptor & {
  version: string
  workflows: Array<{ name: string; description: string }>
  /** 插件是否已在设置中开启 */
  enabled: boolean
  /** 插件必要配置是否已就绪 */
  configured: boolean
  /** 插件代码是否已成功载入后端进程 */
  loaded: boolean
}

export type SkillDescriptor = { id: string; name: string; description: string; source: string; plugin_id?: string; enabled: boolean }

/**
 * 历史会话简略条目（用于侧边栏列表展示）。
 */
export type SessionItem = {
  /** 会话唯一标识 */
  id: string
  /** 会话标题 */
  title: string
  /** 最后修改/更新时间（ISO 字符串） */
  updated: string
}

/**
 * MedPeer 单点登录认证成功后的用户基础资料。
 */
export type UserProfile = {
  /** 用户在平台的唯一主键 ID */
  userID: string | number
  /** 真实姓名 */
  realName: string
  /** 平台登录用户名 */
  userName: string
  /** 头像 URL */
  userAvatar: string
  /** 绑定手机号 */
  userPhone: string
  /** 当前账户研值余额 */
  userCredit: number
  /** 企业机构合作方身份标识 */
  isCo: Json
  /** 内测/公测用户权限标识 */
  isBeta: Json
}

/**
 * 智能体运行时向用户发起的交互式问题（如需确认或需要补充材料）。
 */
export type Question = {
  request_id?: string
  workflow_name?: string
  purpose?: string
  input_scope?: Json
  planned_outputs?: Json
  allowed_effects?: Json
  /** 交互请求全局唯一标识符 */
  interaction_id: string
  /** 发起提问的目标插件标识 */
  plugin_id: string
  /** 提问形态类别（如 approval 授权，或 question 文本补充） */
  kind: string
  /** 具体的提问说明或请求授权内容文本 */
  question: string
}

/**
 * 单项工具调用或智能体流式执行事件记录。
 */
export type RunRecord = {
  seq?: number
  operation_id?: string
  parent_ids?: string[]
  node?: string | null
  agent_name?: string | null
  plugin_id?: string
  workflow_name?: string
  /** 事件类型（如 tool_start, tool_end, text, model_start 等） */
  kind: string
  /** 工具调用唯一标识 */
  call_id?: string
  /** 工具名称或步骤名称 */
  name?: string
  /** 执行状态（如 running, success, failed） */
  status?: string
  /** 模型输出的部分文本增量 */
  text?: Json
  /** 工具调用执行完毕后的结构化返回值 */
  output?: Json
  /** 阶段性统计快照 */
  totals?: Snapshot['totals']
  [key: string]: Json | undefined
}

/**
 * 全局统一模型配置。
 */
export type GlobalModel = {
  /** 统一模型服务提供方，固定为 medpeer */
  provider: 'medpeer' | 'deepseek' | 'harness'
  /** 模型代码（如 agnes-3.0-flash） */
  model: string
}

/**
 * 桌面应用全局生命周期运行状态。
 */
export type ApplicationState = {
  skills: SkillDescriptor[]
  /** 构建运行模式：开发环境 ('development') 或打包生产环境 ('production') */
  build_mode: 'development' | 'production'
  /** 用户本地已注册的项目工作区列表 */
  projects: Array<{ id: string; name: string; path: string | null }>
  /** 当前正处于激活状态的项目 ID */
  active_project_id: string | null
  /** 当前绑定的全局大模型信息 */
  model: GlobalModel | null
  /** Exa 联网搜索引擎配置状态 */
  /** 当前系统加载的插件列表及配置 */
  plugins: AppPlugin[]
  /** 当前登录的用户信息，未登录时为 null */
  user: UserProfile | null
  /** 本机已检测或配置的脚本运行时环境 */
  runtimes: { python: string; node: string }
  /** 工具调用安全执行权限模式：只读 / 操作前询问 / 完全访问 */
  permission_mode: 'read_only' | 'ask' | 'full'
  /** 本机用户数据主目录绝对路径 */
  data_dir: string
  /** 会话上下文最大允许的 Token 限制 */
  max_context_tokens?: number
}

/**
 * 本机 Python 和系统环境运行信息。
 */
export type EnvironmentInfo = {
  /** 实际调用的 Python 解释器完整路径 */
  python_executable: string
  /** Python 版本号 */
  python_version: string
  /** 操作系统平台及架构信息 */
  platform: string
  /** 本机数据持久化目录路径 */
  data_dir: string
  /** 当前绑定工作区物理路径 */
  project_path: string | null
}

/**
 * 用户在对话中附加的文件引用契约。
 */
export type AttachmentRef = {
  /** 附件唯一标识 */
  id: string
  /** 所属工作区 ID */
  workspace_id: string
  /** 文件名 */
  name: string
  /** MIME 媒体格式类型（如 image/png, application/pdf） */
  media_type: string
  /** 文件字节大小 */
  size: number
  /** 在工作区附件目录下的相对路径 */
  relative_path: string
}

/**
 * 智能体运行所产出的交付物文件引用契约。
 */
export type ArtifactRef = {
  /** 交付物唯一标识 */
  id: string
  /** 所属工作区 ID */
  workspace_id: string
  /** 成果文件名称 */
  name: string
  /** MIME 媒体格式类型 */
  media_type: string
  /** 相对工作区交付目录的路径 */
  relative_path: string
  /** 产出该交付物的运行轮次 ID */
  source_run_id: string
}

/**
 * 工作区目录树中的文件或目录条目结构。
 */
export type WorkspaceFileEntry = {
  /** 文件或目录名称 */
  name: string
  /** 本机文件系统完整绝对路径 */
  path: string
  /** 相对于工作区根目录的相对路径（正斜杠格式） */
  relativePath: string
  /** 是否为目录 */
  isDirectory: boolean
  /** 文件字节数（目录一般为 0） */
  size: number
  /** 上次修改时间的毫秒时间戳 */
  mtime: number
}

/**
 * 智能体规则、技能及 MCP 服务配置视图。
 */
export type ConfigView = {
  /** 主智能体提示词配置及其来源 */
  agent: { content: string; source: string }
  /** 技能列表及其启用状态 */
  skills: Array<{ id: string; path: string; source: string; enabled: boolean }>
  /** MCP 服务配置及其来源文件 */
  mcp: { servers: Record<string, Json>; sources: Record<string, string> }
  /** 针对当前项目的局部重载覆盖项 */
  overrides: { agent: string; mcp: string }
}

/**
 * 配置应用范围：全局共享 ('global') 或仅限当前项目 ('project')。
 */
export type ConfigScope = 'global' | 'project'

/**
 * 渲染进程向主进程发起的全部 JSON-RPC 指令契约联合类型。
 */
export type Command =
  | { id: string; method: 'usage.stats'; params: { period: string; model: string } }
  | { id: string; method: 'protocol.hello' | 'session.snapshot' | 'session.list' | 'session.token_usage' | 'session.clear' | 'plugin.list' | 'app.snapshot' | 'app.environment'; params: Record<string, never> }
  | { id: string; method: 'context.configure'; params: { max_tokens: string } }
  | { id: string; method: 'session.search'; params: { query: string } }
  | { id: string; method: 'session.resume'; params: { session_id: string } }
  | { id: string; method: 'plugin.snapshot'; params: { plugin_id: string } }
  | { id: string; method: 'plugin.set'; params: { plugin_id: string; enabled: 'true' | 'false' } }
  | { id: string; method: 'run.start'; params: { text: string; selected_plugins?: string[]; selected_skills?: string[] } }
  | { id: string; method: 'run.attachments'; params: { text: string; attachment_ids: string[]; selected_plugins?: string[]; selected_skills?: string[] } }
  | { id: string; method: 'run.stop'; params: { run_id: string } }
  | { id: string; method: 'interaction.answer'; params: { run_id: string; interaction_id: string; answer: string } }
  | { id: string; method: 'project.create'; params: { name: string } }
  | { id: string; method: 'project.select'; params: { project_id: string } }
  | { id: string; method: 'model.test'; params: Record<string, never> }
  | { id: string; method: 'model.configure'; params: { model_code: string } }
  | { id: string; method: 'runtime.configure'; params: ApplicationState['runtimes'] }
  | { id: string; method: 'permission.set'; params: { mode: ApplicationState['permission_mode'] } }
  | { id: string; method: 'config.inspect'; params: { scope: ConfigScope } }
  | { id: string; method: 'config.save'; params: { scope: ConfigScope; kind: 'agent' | 'mcp'; content: string } }
  | { id: string; method: 'config.remove'; params: { kind: 'agent' | 'mcp' } }
  | { id: string; method: 'skill.read'; params: { scope: ConfigScope; skill_id: string } }
  | { id: string; method: 'skill.save'; params: { scope: ConfigScope; skill_id: string; content: string } }
  | { id: string; method: 'skill.set'; params: { scope: ConfigScope; skill_id: string; enabled: 'true' | 'false' } }
  | { id: string; method: 'mcp.test'; params: { scope: ConfigScope; server_id: string } }
  | { id: string; method: 'session.rename'; params: { session_id: string; title: string } }
  | { id: string; method: 'session.delete'; params: { session_id: string } }
  | { id: string; method: 'session.export'; params: { session_id: string } }

/**
 * 后端向前端广播推送的事件帧契约联合类型。
 */
export type EventFrame =
  | { event: 'ready'; data: Snapshot }
  | { event: 'app.ready'; data: ApplicationState }
  | { event: 'run.event'; session_id: string; run_id: string; data: RunRecord }
  | { event: 'interaction.request'; session_id: string; run_id: string; data: Question }
  | { event: 'run.finished'; session_id: string; run_id: string; data: { status: 'complete' | 'cancelled' | 'failed'; snapshot: Snapshot } }
  | { event: 'backend.exited' | 'protocol.error'; data: { message: string } }

/**
 * 通过 Electron preload contextBridge 向渲染进程暴露的原生能力 API 契约。
 */
export type DesktopAPI = {
  /** 打开系统文件管理器并定位到数据或日志目录 */
  openDataDirectory(kind: 'data' | 'logs'): Promise<void>
  /** 弹出系统原生文件夹选择对话框以绑定工作区目录 */
  selectWorkspace(): Promise<string | null>
  /** 向 Python 后端发送标准 JSON-RPC 命令 */
  command(command: Command): Promise<unknown>
  /** 调起 MedPeer SSO 登录模态窗口 */
  login(): Promise<ApplicationState>
  /** 注销当前用户登录状态并清除本地凭据 */
  logout(): Promise<ApplicationState>
  /** 订阅后端推送事件帧，返回注销监听器的回调函数 */
  onEvent(listener: (event: EventFrame) => void): () => void
  /** 使用系统默认程序打开生成的交付物成果文件 */
  openArtifact(path: string): Promise<void>
  /** 弹出目录选择框并导入技能目录 */
  importSkill(scope: ConfigScope): Promise<ConfigView | null>
  /** 弹出系统文件选择框导入对话附件 */
  importAttachments(): Promise<AttachmentRef[]>
  /** 导入从外部拖拽放入的本地文件列表为对话附件 */
  importDroppedFiles(files: File[]): Promise<AttachmentRef[]>
  /** 使用系统关联程序打开指定的附件文件 */
  openAttachment(id: string): Promise<void>
  /** 读取指定附件的原始 base64 内容与媒体类型 */
  readAttachment(id: string): Promise<{ media_type: string; data: string }>
  /** 读取指定交付成果文件的原始 base64 内容与媒体类型 */
  readArtifact(path: string): Promise<{ media_type: string; data: string }>
  /** 使用系统默认默认浏览器打开外部网页链接 */
  openExternal(url: string): Promise<void>
  /** 导出指定会话的历史记录为本地文本文件 */
  exportSession(sessionId: string): Promise<void>
  /** 递归扫描工作区目录下的文件与文件夹树结构 */
  listWorkspaceDirectory(dirPath?: string): Promise<WorkspaceFileEntry[]>
  /** 使用系统默认应用直接打开工作区文件 */
  openWorkspaceFile(filePath: string): Promise<void>
  /** 在系统资源管理器中定位并高亮指定文件 */
  showItemInFolder(filePath: string): Promise<void>
  /** 在系统资源管理器中打开指定的工作区文件夹 */
  openWorkspaceFolder(folderPath?: string): Promise<void>
  /** 发起工作区文件的原生 OS 文件拖拽传输 */
  dragWorkspaceFile(filePath: string): Promise<void>
  /** 将外部拖拽的文件复制到指定工作区目标目录下 */
  copyDroppedFilesToWorkspace(files: (File | string)[], targetDir?: string): Promise<string[]>
}
