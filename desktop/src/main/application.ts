/**
 * @file application.ts
 * @description Electron 主进程业务应用总控核心。
 * 负责调度会话上下文、管理项目隔离存储（LocalStore / DesktopStore）、
 * 向 Python 后端下发运行配置与任务指令、处理事件流帧转换、以及状态快照收集。
 * @author tongliga
 * @date 2026-10-16
 */

import { existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import path from 'node:path'
import type { ApplicationState, AppPlugin, Command, ConfigScope, EventFrame, Json, Question, RunRecord, SessionTokenReport, SkillDescriptor, Snapshot } from '../shared/protocol'
import type { Backend, EngineFrame, EngineEvent, Interrupt } from './backend'
import { DesktopStore } from './desktop-store'
import { LocalStore, writeAtomic } from './local-store'

/** 后端返回的环境运行时诊断信息 */
type Environment = {
  model: string
  python_executable: string
  node_executable: string
  python_version: string
  node_version: string
  platform: string
}

/** 会话历史已保存轮次与附件索引结构 */
type Saved = {
  runs: Snapshot['runs']
  attachment_ids: string[]
  analysis: Record<string, Json>
  last_run_id: string | null
  active_run?: { id: string; started: number; records: RunRecord[] }
}

/** 内存中维护的当前执行中任务状态 */
type Running = {
  id: string
  thread: string
  started: number
  records: RunRecord[]
  interrupts: Interrupt[]
  answers: Record<string, Json>
}

/** 后端快照指令返回的结构体 */
type SnapshotResult = {
  thread_id: string
  run_id: string | null
  messages: Snapshot['messages']
  interrupts: Interrupt[]
  artifacts: string[]
  token_usage: SessionTokenReport
}

/** 插件与技能目录快照 */
type Catalog = {
  plugins: Array<Pick<AppPlugin, 'id' | 'name' | 'description' | 'version' | 'workflows'>>
  skills: Omit<SkillDescriptor, 'enabled'>[]
}

/** 生成空会话快照兜底对象 */
const emptySnapshot = (): Snapshot => ({
  session_id: '',
  thread_id: '',
  thread_ids: [],
  messages: [],
  attachments: [],
  artifacts: [],
  runs: [],
  totals: {},
  unsettled_history: false
})

/**
 * 桌面端核心业务应用控制器。
 */
export class DesktopApplication {
  /** 本地项目与历史会话索引存储实例 */
  private local: LocalStore
  /** 当前项目或通用偏好数据存储实例 */
  private files: DesktopStore
  /** 后端报告的 Python/Node 运行时环境信息 */
  private environment: Environment | null = null
  /** 当前正在执行或暂停等待交互的任务句柄 */
  private running: Running | null = null
  /** 系统已注册的插件与技能目录 */
  private catalog: Catalog = { plugins: [], skills: [] }
  /** 任务串行化 Promise 执行队列（防止并发写索引导致竞争） */
  private queue: Promise<unknown> = Promise.resolve()

  /**
   * 初始化桌面应用业务控制器。
   *
   * @param directory - 系统应用数据目录 (userData)
   * @param backend - 后端 Python 进程通信客户端
   * @param emit - 向渲染进程推送事件帧的回调函数
   */
  constructor(
    readonly directory: string,
    private backend: Pick<Backend, 'request'>,
    private emit: (frame: EventFrame) => void
  ) {
    this.local = new LocalStore(this.directory)
    const globalDir = path.join(this.directory, 'global')
    const projectDir = this.local.activeProject ? this.local.projectDirectory() : globalDir
    const workspace = this.local.activeProject?.path ?? path.join(projectDir, 'workspace')
    mkdirSync(workspace, { recursive: true })
    this.files = new DesktopStore(projectDir, workspace, globalDir)
  }

  /**
   * 将异步操作推入单线程串行执行队列，确保前置任务完成后才执行下一个动作。
   *
   * @param action - 待执行的异步任务函数
   * @returns 任务执行结果 Promise
   */
  serial<T>(action: () => Promise<T>): Promise<T> {
    const result = this.queue.then(action)
    this.queue = result.then(() => undefined, () => undefined)
    return result
  }

  /**
   * 获取 LocalStore 实例。
   */
  private account(): LocalStore {
    return this.local
  }

  /**
   * 获取当前生效的 DesktopStore 本地数据存储实例。
   */
  storage(): DesktopStore {
    return this.files
  }

  /**
   * 获取当前工作区物理绝对路径。
   */
  workspace(): string {
    return this.storage().workspace
  }

  /**
   * 判断当前是否有任务处于运行中或等待交互状态。
   */
  isRunning(): boolean {
    return this.running !== null
  }

  /**
   * 断言当前处于空闲状态，若有运行中的任务则阻止切换项目或工作区。
   *
   * @throws Error 当正在执行任务时抛出
   */
  private idle(): void {
    if (this.running) throw new Error('请先结束当前任务')
  }

  /**
   * 根据当前激活的项目或通用偏好路径初始化 DesktopStore 存储。
   */
  private selectFiles(): void {
    const local = this.account()
    const globalDir = path.join(this.directory, 'global')
    const directory = local.activeProject ? local.projectDirectory() : globalDir
    const workspace = local.activeProject?.path ?? path.join(directory, 'workspace')
    mkdirSync(workspace, { recursive: true })
    this.files = new DesktopStore(directory, workspace, globalDir)
  }

  /**
   * 初始化应用运行时环境及后端能力目录。
   *
   * @returns 初始化的应用程序全局状态
   */
  async initialize(): Promise<ApplicationState> {
    this.environment = await this.backend.request<Environment>('environment', {})
    this.catalog = await this.backend.request<Catalog>('catalog.get', {})
    await this.configurePreferences()
    return this.state()
  }

  /**
   * 组装包含启用与授权状态的可用插件列表。
   */
  private plugins(): AppPlugin[] {
    const settings = this.files.readSettings()
    const enabled = settings.plugins ?? {}
    const authorized = this.files.authorizedPlugins()
    return this.catalog.plugins.map(plugin => ({
      ...plugin,
      interaction_kinds: ['plugin_authorization'],
      enabled: enabled[plugin.id] === true,
      authorized: authorized.includes(plugin.id),
      configured: true,
      loaded: enabled[plugin.id] === true
    }))
  }

  /**
   * 聚合生成前端所需的当前全局 ApplicationState 应用快照。
   */
  state(): ApplicationState {
    const settings = this.files.readSettings()
    const model = settings.model_code || this.environment?.model
    return {
      build_mode: 'development',
      projects: this.local.projects,
      active_project_id: this.local.activeProject?.id ?? null,
      model: model ? {
        provider: model.startsWith('deepseek:') ? 'deepseek' : 'harness',
        model: model.replace(/^deepseek:/, '')
      } : null,
      plugins: this.plugins(),
      skills: this.catalog.skills.map(skill => ({
        ...skill,
        enabled: !skill.plugin_id || settings.plugins[skill.plugin_id] === true
      })),
      runtimes: settings.runtimes,
      permission_mode: settings.permission_mode,
      data_dir: this.directory,
      max_context_tokens: settings.max_context_tokens
    }
  }

  /**
   * 将当前项目的设置、自定义提示词、启用的技能及 MCP 配置同步发送至后端。
   */
  private async configure(): Promise<void> {
    const local = this.account(), files = this.storage(), settings = files.readSettings()
    const config = files.inspectConfig('project')
    const globalSkills = files.inspectConfig('global').skills
    const skills = config.skills.filter(skill => skill.enabled).map(skill => ({
      id: skill.id,
      content: files.readSkill('project', skill.id).content,
      source: globalSkills.some(global => global.id === skill.id && global.source === skill.source) ? '全局' : '项目'
    }))
    await this.backend.request('configure', {
      tenant_id: local.activeProject!.id,
      user_id: 'default',
      workspace_root: this.workspace(),
      runtime_data_dir: local.projectDirectory(),
      options: {
        model_code: settings.model_code || this.environment!.model,
        runtimes: settings.runtimes,
        permission_mode: settings.permission_mode,
        max_context_tokens: settings.max_context_tokens,
        agent_content: config.agent.content,
        skills,
        mcp_servers: config.mcp.servers,
        authorized_plugins: files.authorizedPlugins(),
        enabled_plugins: Object.keys(settings.plugins).filter(id => settings.plugins[id] && this.catalog.plugins.some(plugin => plugin.id === id))
      }
    })
    this.catalog = await this.backend.request<Catalog>('catalog.get', {})
  }

  /**
   * 将全局或通用偏好配置同步发送至后端。
   *
   * @param scope - 配置范围，默认为项目级
   */
  private async configurePreferences(scope: ConfigScope = 'project'): Promise<void> {
    if (scope === 'project' && this.account().activeProject) {
      await this.configure()
      return
    }
    const files = this.storage(), settings = files.readSettings(), config = files.inspectConfig('global')
    await this.backend.request('configure', {
      tenant_id: 'preferences',
      user_id: 'default',
      workspace_root: files.workspace,
      runtime_data_dir: files.directory,
      options: {
        model_code: settings.model_code || this.environment!.model,
        runtimes: settings.runtimes,
        permission_mode: settings.permission_mode,
        max_context_tokens: settings.max_context_tokens,
        agent_content: config.agent.content,
        skills: config.skills.filter(item => item.enabled).map(item => ({
          id: item.id,
          content: files.readSkill('global', item.id).content,
          source: '全局'
        })),
        mcp_servers: config.mcp.servers,
        authorized_plugins: files.authorizedPlugins(),
        enabled_plugins: Object.keys(settings.plugins).filter(id => settings.plugins[id] && this.catalog.plugins.some(plugin => plugin.id === id))
      }
    })
    this.catalog = await this.backend.request<Catalog>('catalog.get', {})
  }

  /** 获取指定会话已保存执行历史 JSON 文件的绝对物理路径 */
  private recordPath(session: string): string {
    this.account().session(session)
    return path.join(this.account().projectDirectory(), 'sessions', `${session}.json`)
  }

  /** 读取指定会话的历史保存记录 */
  private saved(session: string): Saved {
    const file = this.recordPath(session)
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) as Saved : { runs: [], attachment_ids: [], analysis: {}, last_run_id: null }
  }

  /** 原子持久化指定会话的执行历史记录 */
  private save(session: string, value: Saved): void {
    writeAtomic(this.recordPath(session), JSON.stringify(value))
  }

  /** 统计指定会话累计 Token 与模型用量 */
  private totals(sessionId: string): Snapshot['totals'] {
    const usage = this.storage().usageStats('', '', { projectId: this.account().activeProject!.id, sessionId })
    return {
      ...usage.totals,
      models: usage.models.map(model => ({ ...model, cache_hit_tokens: model.cache_read })),
      complete: usage.totals.requests === usage.totals.reported_requests
    }
  }
  /**
   * 捕获并拼装当前会话的最新完整快照。
   *
   * 第一步：同步项目配置并向后端请求会话消息与中断状态。
   * 第二步：提取消息中的 Token 消耗并持久化记录至用量统计。
   * 第三步：核对工作区交付物成果文件，自动登记新生成的交付物。
   * 第四步：组装消息流、附件列表、历史轮次记录及 Token 统计并返回。
   *
   * @param sessionId - 可选的指定会话 ID（默认使用当前活跃会话）
   * @param restoreInterrupts - 是否恢复未完成中断状态至内存 running 句柄中
   * @returns 组装好的会话完整快照对象
   */
  private async snapshot(sessionId?: string, restoreInterrupts = true): Promise<Snapshot> {
    const local = this.account()
    if (!local.activeProject) return emptySnapshot()
    const session = sessionId ? local.session(sessionId) : local.currentSession()
    await this.configure()
    const result = await this.backend.request<SnapshotResult>('snapshot', { thread_id: session.id })
    const saved = this.saved(session.id)

    // 记录 AI 回复中的 Token 消耗信息
    for (const message of result.messages.filter(message => message.type === 'ai')) {
      const usage = message.data.usage_metadata as { input_tokens: number; output_tokens: number; total_tokens: number } | null
      const metadata = message.data.response_metadata as Record<string, Json> | undefined
      if (message.data.id) {
        this.storage().recordUsage(local.activeProject!.id, session.id, {
          request_id: message.data.id as string,
          requested_at: new Date().toISOString(),
          model: metadata?.model_name as string || metadata?.model as string || this.state().model!.model,
          source: 'model',
          usage: usage ?? null,
          cost_estimate: null
        })
      }
    }

    // 扫描登记成果文件
    const runIds = new Set([...saved.runs.map(run => run.id), saved.last_run_id])
    const published = this.storage().listArtifacts()
    const artifacts = published.filter(item => runIds.has(item.source_run_id))
    for (const file of result.artifacts) {
      const relative = `/workspace/${file}`
      const existing = published.find(item => item.relative_path === relative)
      if (artifacts.some(item => item.relative_path === relative)) continue
      if (existing) {
        artifacts.push(existing)
        continue
      }
      if (!saved.last_run_id) throw new Error('报告缺少对应的任务记录')
      artifacts.push(this.storage().publishArtifact(relative, saved.last_run_id))
    }

    const totals = this.totals(session.id)
    const snapshot: Snapshot = {
      session_id: session.id,
      thread_id: session.id,
      thread_ids: [session.id],
      messages: result.messages,
      attachments: saved.attachment_ids.map(id => this.storage().attachment(id)),
      artifacts,
      runs: saved.runs,
      totals,
      unsettled_history: result.interrupts.length > 0,
      token_usage: result.token_usage
    }

    if (restoreInterrupts && !this.running && !result.interrupts.length && saved.active_run) {
      const userMessage = [...result.messages].reverse().find(message => message.type === 'human')
      this.saveRun({ ...saved.active_run, thread: session.id }, 'failed', saved, userMessage?.data.id as string | undefined)
    }

    if (result.interrupts.length && saved.active_run) {
      snapshot.active_run = {
        id: saved.active_run.id,
        started_at: new Date(saved.active_run.started).toISOString(),
        records: saved.active_run.records,
        user_message_id: [...result.messages].reverse().find(message => message.type === 'human')?.data.id as string | undefined
      }
    }

    if (restoreInterrupts && result.interrupts.length && !this.running) {
      if (!result.run_id) throw new Error('暂停任务缺少运行 ID')
      this.running = {
        id: result.run_id,
        thread: session.id,
        started: saved.active_run?.started ?? Date.now(),
        records: saved.active_run?.records ?? [],
        interrupts: result.interrupts,
        answers: {}
      }
    }
    return snapshot
  }

  /**
   * 从当前挂起的中断列表中提取待处理的问题交互数据。
   */
  private question(): Question {
    const item = this.running!.interrupts.find(interrupt => !(interrupt.id in this.running!.answers))!
    const actions = item.value.action_requests
    if (item.value.kind === 'plugin_authorization') return { ...item.value, interaction_id: item.id } as Question
    const options = Array.isArray(item.value.options) && item.value.options.every(option => typeof option === 'string') ? item.value.options as string[] : undefined
    return {
      interaction_id: item.id,
      plugin_id: typeof item.value.plugin_id === 'string' ? item.value.plugin_id : 'harness',
      kind: actions ? 'approval' : 'question',
      options,
      action_requests: actions,
      question: actions ? '以下操作需要你的确认，允许后将继续执行。' : String(item.value.question)
    }
  }

  /**
   * 向前端派发交互请求事件通知。
   */
  private notifyQuestion(): void {
    if (this.running?.interrupts.some(item => !(item.id in this.running!.answers))) {
      this.emit({
        event: 'interaction.request',
        session_id: this.running.thread,
        run_id: this.running.id,
        data: this.question()
      })
    }
  }

  /**
   * 将本地文件系统中的目录绑定至当前应用工作区。
   *
   * @param workspace - 本地工作区物理路径
   * @returns 绑定的工作区路径
   */
  async bindWorkspace(workspace: string): Promise<string> {
    this.idle()
    const local = this.account()
    const existing = local.projects.find(project => project.path === workspace)
    if (existing) {
      local.selectProject(existing.id)
    } else {
      const project = local.createProject(path.basename(workspace))
      local.bindProject(project.id, workspace)
    }
    this.selectFiles()
    this.emit({ event: 'app.ready', data: this.state() })
    this.emit({ event: 'ready', data: await this.snapshot() })
    this.notifyQuestion()
    return workspace
  }

  /**
   * 统一调度并执行来自渲染进程的前端命令。
   *
   * @param command - 结构化 JSON-RPC 指令
   * @returns 指令执行返回结果
   */
  async command(command: Command): Promise<unknown> {
    const { method, params } = command
    if (method === 'protocol.hello') return { protocol: '2.0' }
    if (method === 'app.snapshot') {
      if (!this.running) await this.configurePreferences()
      return this.state()
    }
    if (method === 'app.environment') return { ...this.environment, data_dir: this.directory, project_path: this.local.activeProject?.path ?? null }
    if (method === 'session.snapshot' && !this.local.activeProject) return emptySnapshot()

    const local = this.local, files = this.files
    switch (method) {
      case 'session.list':
        return local.sessions()
      case 'session.search':
        return local.sessions(params.query)
      case 'plugin.list':
        return this.plugins()
      case 'plugin.snapshot': {
        if (!this.catalog.plugins.some(plugin => plugin.id === params.plugin_id)) throw new Error('未知插件')
        return {
          plugin_id: params.plugin_id,
          state: {
            runs: local.activeProject ? this.saved(local.currentSession().id).runs.map(run => ({
              ...run,
              records: run.records?.filter(record => record.plugin_id === params.plugin_id)
            })) : []
          }
        }
      }
      case 'session.snapshot': {
        this.idle()
        const value = await this.snapshot()
        setImmediate(() => {
          this.emit({ event: 'ready', data: value })
          this.notifyQuestion()
        })
        return value
      }
      case 'project.create':
        this.idle()
        if (!params.name.trim()) throw new Error('项目名称不能为空')
        local.createProject(params.name)
        this.selectFiles()
        this.emit({ event: 'ready', data: await this.snapshot() })
        this.emit({ event: 'app.ready', data: this.state() })
        return this.state()
      case 'project.select':
        this.idle()
        local.selectProject(params.project_id)
        this.selectFiles()
        this.emit({ event: 'ready', data: await this.snapshot() })
        this.notifyQuestion()
        this.emit({ event: 'app.ready', data: this.state() })
        return this.state()
      case 'session.resume':
        this.idle()
        local.resume(params.session_id)
        {
          const value = await this.snapshot()
          setImmediate(() => this.notifyQuestion())
          return value
        }
      case 'session.clear':
        this.idle()
        local.createSession()
        return this.snapshot()
      case 'session.rename':
        if (!params.title.trim()) throw new Error('会话标题不能为空')
        local.rename(params.session_id, params.title)
        return local.sessions()
      case 'session.delete': {
        this.idle()
        local.session(params.session_id)
        await this.configure()
        await this.backend.request('session.delete', { thread_id: params.session_id })
        const file = this.recordPath(params.session_id)
        local.deleteSession(params.session_id)
        rmSync(file, { force: true })
        this.emit({ event: 'ready', data: await this.snapshot() })
        return local.sessions()
      }
      case 'session.export': {
        this.idle()
        const value = await this.snapshot(params.session_id, false)
        return {
          text: value.messages.map(message => `${message.type === 'human' ? '用户' : '助手'}：\n${typeof message.data.content === 'string' ? message.data.content : JSON.stringify(message.data.content)}\n`).join('\n')
        }
      }
      case 'session.token_usage':
        if (!local.activeProject) throw new Error('请先选择项目')
        await this.configure()
        return this.backend.request('session.token_usage', { thread_id: local.currentSession().id })
      case 'run.start':
      case 'run.attachments': {
        this.idle()
        if (!params.text.trim()) throw new Error('任务不能为空')
        const attachments = method === 'run.attachments' ? command.params.attachment_ids.map(id => files.attachment(id)) : []
        const session = local.currentSession()
        await this.configure()
        const selected_plugins = params.selected_plugins ?? [], selected_skills = params.selected_skills ?? []
        if (selected_plugins.some(id => !this.plugins().some(plugin => plugin.id === id && plugin.enabled))) throw new Error('选中的插件未启用')
        if (selected_skills.some(id => !this.state().skills.some(skill => skill.id === id && skill.enabled))) throw new Error('选中的技能不可用')
        this.running = { id: command.id, thread: session.id, started: Date.now(), records: [], interrupts: [], answers: {} }
        try {
          await this.backend.request('run.start', {
            thread_id: session.id,
            run_id: command.id,
            text: params.text,
            selected_plugins,
            selected_skills,
            attachments: attachments.map(item => ({
              name: item.name,
              media_type: item.media_type,
              path: files.resolveAttachment(item.id)
            }))
          })
        } catch (error) {
          this.running = null
          throw error
        }
        const saved = this.saved(session.id)
        saved.last_run_id = command.id
        saved.attachment_ids = [...new Set([...saved.attachment_ids, ...attachments.map(item => item.id)])]
        saved.active_run = { id: command.id, started: this.running.started, records: [] }
        this.save(session.id, saved)
        local.touch(session.id, params.text)
        return { run_id: command.id }
      }
      case 'run.stop':
        if (!this.running || this.running.id !== params.run_id) throw new Error('当前任务不匹配')
        await this.backend.request('run.stop', { run_id: params.run_id, thread_id: this.running.thread })
        if (this.running?.interrupts.length) {
          setImmediate(() => {
            void this.serial(() => this.finish('cancelled')).catch(error => this.emit({ event: 'protocol.error', data: { message: String(error) } }))
          })
        }
        return {}
      case 'interaction.answer': {
        const run = this.running
        if (!run || run.id !== params.run_id) throw new Error('交互请求已失效')
        const interrupt = run.interrupts.find(item => item.id === params.interaction_id && !(item.id in run.answers))
        if (!interrupt) throw new Error('交互请求已失效')
        let answer: Json
        if (interrupt.value.kind === 'plugin_authorization') {
          if (!['approve', 'reject'].includes(params.answer)) throw new Error('无效审批决定')
          answer = { request_id: interrupt.value.request_id!, approved: params.answer === 'approve' }
        } else if (interrupt.value.action_requests) {
          if (!['approve', 'reject'].includes(params.answer)) throw new Error('无效审批决定')
          answer = { decisions: interrupt.value.action_requests.map(() => ({ type: params.answer })) }
        } else {
          if (!params.answer.trim()) throw new Error('答复不能为空')
          answer = params.answer
        }
        const answers = { ...run.answers, [interrupt.id]: answer }
        if (!run.interrupts.every(item => item.id in answers)) {
          run.answers = answers
          setImmediate(() => this.notifyQuestion())
          return {}
        }
        await this.backend.request('run.resume', { thread_id: run.thread, run_id: run.id, decisions: answers })
        const approvedPlugins = run.interrupts.filter(item => item.value.kind === 'plugin_authorization' && (answers[item.id] as { approved?: boolean })?.approved === true).map(item => item.value.plugin_id).filter((id): id is string => typeof id === 'string')
        if (approvedPlugins.length) {
          files.authorizePlugins(approvedPlugins)
          this.emit({ event: 'app.ready', data: this.state() })
        }
        run.interrupts = []
        run.answers = {}
        return { run_id: run.id }
      }
      case 'model.configure':
        this.idle()
        if (!params.model_code.trim()) throw new Error('模型不能为空')
        files.saveSettings({ model_code: params.model_code })
        break
      case 'model.test':
        this.idle()
        await this.configurePreferences()
        await this.backend.request('model.test', {})
        return this.state()
      case 'context.configure': {
        this.idle()
        const max = Number(params.max_tokens)
        if (!Number.isSafeInteger(max) || max < 1 || max > 475000) throw new Error('上下文窗口必须为 1 到 475,000 之间的整数')
        files.saveSettings({ max_context_tokens: max })
        break
      }
      case 'runtime.configure':
        this.idle()
        files.saveSettings({ runtimes: { python: params.python, node: params.node } })
        break
      case 'permission.set':
        this.idle()
        if (!['read_only', 'ask', 'full'].includes(params.mode)) throw new Error('权限模式无效')
        files.saveSettings({ permission_mode: params.mode })
        break
      case 'config.inspect':
        return files.inspectConfig(params.scope)
      case 'config.save':
        this.idle()
        return files.saveConfig(params.scope, params.kind, params.content)
      case 'config.remove':
        this.idle()
        return files.removeConfig(params.kind)
      case 'skill.read':
        return files.readSkill(params.scope, params.skill_id)
      case 'skill.save':
        this.idle()
        return files.saveSkill(params.scope, params.skill_id, params.content)
      case 'skill.set':
        this.idle()
        if (!['true', 'false'].includes(params.enabled)) throw new Error('技能状态无效')
        return files.setSkill(params.scope, params.skill_id, params.enabled === 'true')
      case 'mcp.test':
        this.idle()
        await this.configurePreferences(params.scope)
        return this.backend.request('mcp.test', { server_id: params.server_id })
      case 'plugin.set': {
        this.idle()
        if (!this.catalog.plugins.some(plugin => plugin.id === params.plugin_id) || !['true', 'false'].includes(params.enabled)) throw new Error('无效插件设置')
        files.saveSettings({ plugins: { ...files.readSettings().plugins, [params.plugin_id]: params.enabled === 'true' } })
        await this.configurePreferences()
        break
      }
      case 'usage.stats':
        return files.usageStats(params.period, params.model)
      default:
        throw new Error(`未实现的桌面命令：${method}`)
    }
    this.emit({ event: 'app.ready', data: this.state() })
    return this.state()
  }

  /**
   * 接收并处理来自 Python 后端引擎推送的事件帧。
   *
   * @param frame - 引擎底层事件帧
   */
  receive(frame: EngineFrame): void {
    void this.serial(async () => {
      if (frame.event === 'backend.exited') {
        if (this.running) {
          const saved = this.saved(this.running.thread)
          saved.active_run = { id: this.running.id, started: this.running.started, records: this.running.records }
          this.save(this.running.thread, saved)
        }
        this.running = null
        this.emit(frame)
        return
      }
      if (!this.running || this.running.id !== frame.data.run_id || this.running.thread !== frame.data.thread_id) return
      if (frame.event === 'run.cancelled') {
        await this.finish('cancelled')
        return
      }
      const event = frame.data
      if (event.type === 'run.interrupted') {
        this.running.interrupts = event.data.interrupts as unknown as Interrupt[]
        const saved = this.saved(this.running.thread)
        saved.active_run = { id: this.running.id, started: this.running.started, records: this.running.records }
        this.save(this.running.thread, saved)
        this.notifyQuestion()
        return
      }
      if (event.type === 'run.completed' || event.type === 'run.failed') {
        if (event.type === 'run.failed') {
          this.emit({ event: 'protocol.error', data: { message: String(event.data.message ?? event.data.error_type) } })
        }
        await this.finish(event.type === 'run.completed' ? 'complete' : 'failed')
        return
      }
      const record = this.convert(event)
      if (event.type === 'model.completed') {
        const usage = event.data.usage as { input_tokens: number; output_tokens: number; total_tokens: number } | undefined
        this.storage().recordUsage(this.account().activeProject!.id, this.running.thread, {
          request_id: event.data.message_id as string || event.operation_id,
          requested_at: event.data.requested_at as string || new Date().toISOString(),
          model: event.data.model as string || this.state().model!.model,
          source: 'model',
          usage: usage ?? null,
          cost_estimate: null
        })
        record.totals = this.totals(this.running.thread)
      }
      this.running.records.push(record)
      this.emit({ event: 'run.event', session_id: this.running.thread, run_id: this.running.id, data: record })
    }).catch(error => {
      this.emit({ event: 'protocol.error', data: { message: String(error) } })
    })
  }

  /**
   * 将后端引擎事件格式转化为前端展示统一的 RunRecord 对象。
   */
  private convert(event: EngineEvent): RunRecord {
    const kind: Record<string, string> = {
      'model.delta': 'text',
      'model.started': 'model_start',
      'model.completed': 'model_end',
      'tool.started': 'tool_start',
      'tool.completed': 'tool_end',
      'tool.failed': 'tool_end'
    }
    return {
      kind: event.type === 'model.delta' && event.agent_name && event.agent_name !== 'vertical-agent' ? 'model_delta' : kind[event.type] ?? event.type,
      name: event.name,
      seq: event.seq,
      operation_id: event.operation_id,
      parent_ids: event.parent_ids,
      node: event.node,
      agent_name: event.agent_name,
      ...(event.type.startsWith('tool.') ? {
        call_id: event.operation_id,
        status: event.type === 'tool.failed' ? 'failed' : event.type === 'tool.completed' ? 'success' : 'running'
      } : {}),
      ...event.data
    }
  }

  /**
   * 结束当前运行轮次，组装耗时、工具执行详情并持久化保存。
   *
   * @param status - 终态标识 ('complete' 成功、'failed' 失败或 'cancelled' 取消)
   */
  private async finish(status: 'complete' | 'failed' | 'cancelled'): Promise<void> {
    const run = this.running!
    const snapshot = await this.snapshot(run.thread, false)
    const saved = this.saved(run.thread)
    const userMessage = [...snapshot.messages].reverse().find(message => message.type === 'human')
    this.saveRun(run, status, saved, userMessage?.data.id as string | undefined)
    snapshot.runs = saved.runs
    this.running = null
    this.emit({ event: 'run.finished', session_id: run.thread, run_id: run.id, data: { status, snapshot } })
  }

  private saveRun(run: Pick<Running, 'id' | 'thread' | 'started' | 'records'>, status: 'complete' | 'failed' | 'cancelled', saved: Saved, userMessageId?: string): void {
    const records = run.records as Json[]
    const requests = new Map(run.records.filter(record => record.kind === 'model_start' || record.kind === 'model_end').map(record => [record.operation_id, record]))
    saved.runs.push({
      id: run.id,
      started_at: new Date(run.started).toISOString(),
      finished_at: new Date().toISOString(),
      status,
      records: run.records,
      user_message_id: userMessageId,
      requests: [...requests.values()] as Json[],
      tools: Object.fromEntries(run.records.flatMap((record, i) => record.call_id ? [[record.call_id, records[i]]] : [])),
      elapsed: (Date.now() - run.started) / 1000
    })
    delete saved.active_run
    this.save(run.thread, saved)
    this.account().touch(run.thread)
  }
}

