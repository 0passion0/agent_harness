import { existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import path from 'node:path'
import type { ApplicationState, AppPlugin, Command, ConfigScope, EventFrame, Json, Question, RunRecord, SessionTokenReport, SkillDescriptor, Snapshot, UserProfile } from '../shared/protocol'
import type { Backend, EngineFrame, EngineEvent, Interrupt } from './backend'
import { DesktopStore } from './desktop-store'
import { LocalStore, writeAtomic } from './local-store'

type Environment = { model: string; python_executable: string; node_executable: string; python_version: string; node_version: string; platform: string }
type Saved = { runs: Snapshot['runs']; attachment_ids: string[]; analysis: Record<string, Json>; last_run_id: string | null; active_run?: { id: string; started: number; records: RunRecord[] } }
type Running = { id: string; thread: string; started: number; records: RunRecord[]; interrupts: Interrupt[]; answers: Record<string, Json> }
type SnapshotResult = { thread_id: string; run_id: string | null; messages: Snapshot['messages']; interrupts: Interrupt[]; artifacts: string[]; token_usage: SessionTokenReport }
type Catalog = { plugins: Array<Pick<AppPlugin, 'id' | 'name' | 'description' | 'version' | 'workflows'>>; skills: Omit<SkillDescriptor, 'enabled'>[] }
const emptySnapshot = (): Snapshot => ({ session_id: '', thread_id: '', thread_ids: [], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false })

export class DesktopApplication {
  private local: LocalStore | null = null
  private files: DesktopStore | null = null
  private environment: Environment | null = null
  private running: Running | null = null
  private catalog: Catalog = { plugins: [], skills: [] }
  private queue: Promise<unknown> = Promise.resolve()
  constructor(readonly directory: string, private backend: Pick<Backend, 'request'>, private user: () => UserProfile | null, private emit: (frame: EventFrame) => void) {}

  serial<T>(action: () => Promise<T>): Promise<T> {
    const result = this.queue.then(action)
    this.queue = result.then(() => undefined, () => undefined)
    return result
  }
  private account(): LocalStore {
    if (!this.local || !this.user()) throw new Error('请先登录')
    return this.local
  }
  storage(): DesktopStore { if (!this.files) throw new Error('请先登录'); return this.files }
  workspace(): string { return this.storage().workspace }
  isRunning(): boolean { return this.running !== null }
  private idle(): void { if (this.running) throw new Error('请先结束当前任务') }
  private selectFiles(): void {
    const local = this.account()
    const directory = local.activeProject ? local.projectDirectory() : path.join(local.directory, 'preferences')
    const workspace = local.activeProject?.path ?? path.join(directory, 'workspace')
    mkdirSync(workspace, { recursive: true })
    this.files = new DesktopStore(directory, workspace, local.directory)
  }
  async initialize(): Promise<ApplicationState> {
    this.environment = await this.backend.request<Environment>('environment', {})
    if (this.user()) { this.local = new LocalStore(this.directory, Number(this.user()!.userID)); this.selectFiles() }
    this.catalog = await this.backend.request<Catalog>('catalog.get', {})
    if (this.user()) await this.configurePreferences()
    return this.state()
  }
  async accountChanged(): Promise<ApplicationState> {
    this.idle()
    this.local = null; this.files = null
    if (this.user()) { this.local = new LocalStore(this.directory, Number(this.user()!.userID)); this.selectFiles() }
    if (this.user()) await this.configurePreferences()
    const state = this.state()
    this.emit({ event: 'app.ready', data: state })
    this.emit({ event: 'ready', data: this.local?.activeProject ? await this.snapshot() : emptySnapshot() })
    return state
  }
  private plugins(): AppPlugin[] {
    const settings = this.files?.readSettings()
    const enabled = settings?.plugins ?? {}
    const authorized = this.files?.authorizedPlugins() ?? []
    return this.catalog.plugins.map(plugin => ({ ...plugin, interaction_kinds: ['plugin_authorization'], enabled: enabled[plugin.id] === true, authorized: authorized.includes(plugin.id), configured: true, loaded: enabled[plugin.id] === true }))
  }
  state(): ApplicationState {
    const settings = this.files?.readSettings()
    const model = settings?.model_code || this.environment?.model
    return {
      build_mode: 'development', user: this.user(), projects: this.local?.projects ?? [], active_project_id: this.local?.activeProject?.id ?? null,
      model: model ? { provider: model.startsWith('deepseek:') ? 'deepseek' : 'harness', model: model.replace(/^deepseek:/, '') } : null,
      plugins: this.plugins(), skills: this.files ? this.catalog.skills.map(skill => ({ ...skill, enabled: !skill.plugin_id || settings?.plugins[skill.plugin_id] === true })) : [],
      runtimes: settings?.runtimes ?? { python: '', node: '' }, permission_mode: settings?.permission_mode ?? 'ask',
      data_dir: this.directory, max_context_tokens: settings?.max_context_tokens ?? 128000
    }
  }
  private async configure(): Promise<void> {
    const local = this.account(), files = this.storage(), settings = files.readSettings()
    const config = files.inspectConfig('project')
    const globalSkills = files.inspectConfig('global').skills
    const skills = config.skills.filter(skill => skill.enabled).map(skill => ({ id: skill.id, content: files.readSkill('project', skill.id).content, source: globalSkills.some(global => global.id === skill.id && global.source === skill.source) ? '全局' : '项目' }))
    await this.backend.request('configure', {
      tenant_id: local.activeProject!.id, user_id: String(this.user()!.userID), workspace_root: this.workspace(), runtime_data_dir: local.projectDirectory(),
      options: { model_code: settings.model_code || this.environment!.model,
        runtimes: settings.runtimes, permission_mode: settings.permission_mode, max_context_tokens: settings.max_context_tokens,
        agent_content: config.agent.content, skills, mcp_servers: config.mcp.servers, authorized_plugins: files.authorizedPlugins(),
        enabled_plugins: Object.keys(settings.plugins).filter(id => settings.plugins[id] && this.catalog.plugins.some(plugin => plugin.id === id)) }
    })
    this.catalog = await this.backend.request<Catalog>('catalog.get', {})
  }
  private async configurePreferences(scope: ConfigScope = 'project'): Promise<void> {
    if (scope === 'project' && this.account().activeProject) { await this.configure(); return }
    const files = this.storage(), settings = files.readSettings(), config = files.inspectConfig('global')
    await this.backend.request('configure', {
      tenant_id: 'preferences', user_id: String(this.user()!.userID), workspace_root: files.workspace, runtime_data_dir: files.directory,
      options: { model_code: settings.model_code || this.environment!.model, runtimes: settings.runtimes,
        permission_mode: settings.permission_mode, max_context_tokens: settings.max_context_tokens, agent_content: config.agent.content,
        skills: config.skills.filter(item => item.enabled).map(item => ({ id: item.id, content: files.readSkill('global', item.id).content, source: '全局' })),
        mcp_servers: config.mcp.servers, authorized_plugins: files.authorizedPlugins(), enabled_plugins: Object.keys(settings.plugins).filter(id => settings.plugins[id] && this.catalog.plugins.some(plugin => plugin.id === id)) }
    })
    this.catalog = await this.backend.request<Catalog>('catalog.get', {})
  }
  private recordPath(session: string): string { this.account().session(session); return path.join(this.account().projectDirectory(), 'sessions', `${session}.json`) }
  private saved(session: string): Saved {
    const file = this.recordPath(session)
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) as Saved : { runs: [], attachment_ids: [], analysis: {}, last_run_id: null }
  }
  private save(session: string, value: Saved): void { writeAtomic(this.recordPath(session), JSON.stringify(value)) }
  private totals(sessionId: string): Snapshot['totals'] {
    const usage = this.storage().usageStats('', '', { projectId: this.account().activeProject!.id, sessionId })
    return { ...usage.totals, models: usage.models.map(model => ({ ...model, cache_hit_tokens: model.cache_read })), complete: usage.totals.requests === usage.totals.reported_requests }
  }
  private async snapshot(sessionId?: string, restoreInterrupts = true): Promise<Snapshot> {
    const local = this.account()
    if (!local.activeProject) return emptySnapshot()
    const session = sessionId ? local.session(sessionId) : local.currentSession()
    await this.configure()
    const result = await this.backend.request<SnapshotResult>('snapshot', { thread_id: session.id })
    const saved = this.saved(session.id)
    for (const message of result.messages.filter(message => message.type === 'ai')) {
      const usage = message.data.usage_metadata as { input_tokens: number; output_tokens: number; total_tokens: number } | null
      const metadata = message.data.response_metadata as Record<string, Json> | undefined
      if (message.data.id) this.storage().recordUsage(local.activeProject!.id, session.id, { request_id: message.data.id as string, requested_at: new Date().toISOString(), model: metadata?.model_name as string || metadata?.model as string || this.state().model!.model, source: 'model', usage: usage ?? null, cost_estimate: null })
    }
    const runIds = new Set([...saved.runs.map(run => run.id), saved.last_run_id])
    const published = this.storage().listArtifacts()
    const artifacts = published.filter(item => runIds.has(item.source_run_id))
    for (const file of result.artifacts) {
      const relative = `/workspace/${file}`
      const existing = published.find(item => item.relative_path === relative)
      if (artifacts.some(item => item.relative_path === relative)) continue
      if (existing) { artifacts.push(existing); continue }
      if (!saved.last_run_id) throw new Error('报告缺少对应的任务记录')
      artifacts.push(this.storage().publishArtifact(relative, saved.last_run_id))
    }
    const totals = this.totals(session.id)
    const snapshot: Snapshot = { session_id: session.id, thread_id: session.id, thread_ids: [session.id], messages: result.messages, attachments: saved.attachment_ids.map(id => this.storage().attachment(id)), artifacts, runs: saved.runs, totals, unsettled_history: result.interrupts.length > 0, token_usage: result.token_usage }
    if (result.interrupts.length && saved.active_run) snapshot.active_run = { id: saved.active_run.id, started_at: new Date(saved.active_run.started).toISOString(), records: saved.active_run.records, user_message_id: [...result.messages].reverse().find(message => message.type === 'human')?.data.id as string | undefined }
    if (restoreInterrupts && result.interrupts.length && !this.running) {
      if (!result.run_id) throw new Error('暂停任务缺少运行 ID')
      this.running = { id: result.run_id, thread: session.id, started: saved.active_run?.started ?? Date.now(), records: saved.active_run?.records ?? [], interrupts: result.interrupts, answers: {} }
    }
    return snapshot
  }
  private question(): Question {
    const item = this.running!.interrupts.find(interrupt => !(interrupt.id in this.running!.answers))!
    const actions = item.value.action_requests
    if (item.value.kind === 'plugin_authorization') return { ...item.value, interaction_id: item.id } as Question
    const options = Array.isArray(item.value.options) && item.value.options.every(option => typeof option === 'string') ? item.value.options as string[] : undefined
    return { interaction_id: item.id, plugin_id: typeof item.value.plugin_id === 'string' ? item.value.plugin_id : 'harness', kind: actions ? 'approval' : 'question', options, action_requests: actions, question: actions ? '以下操作需要你的确认，允许后将继续执行。' : String(item.value.question) }
  }
  private notifyQuestion(): void {
    if (this.running?.interrupts.some(item => !(item.id in this.running!.answers))) this.emit({ event: 'interaction.request', session_id: this.running.thread, run_id: this.running.id, data: this.question() })
  }
  async bindWorkspace(workspace: string): Promise<string> {
    this.idle()
    const local = this.account()
    const existing = local.projects.find(project => project.path === workspace)
    if (existing) local.selectProject(existing.id)
    else { const project = local.createProject(path.basename(workspace)); local.bindProject(project.id, workspace) }
    this.selectFiles()
    this.emit({ event: 'app.ready', data: this.state() })
    this.emit({ event: 'ready', data: await this.snapshot() })
    this.notifyQuestion()
    return workspace
  }
  async command(command: Command): Promise<unknown> {
    const { method, params } = command
    if (method === 'protocol.hello') return { protocol: '2.0' }
    if (method === 'app.snapshot') { if (this.user() && !this.running) await this.configurePreferences(); return this.state() }
    if (method === 'app.environment') return { ...this.environment, data_dir: this.directory, project_path: this.local?.activeProject?.path ?? null }
    if (method === 'session.snapshot' && !this.local?.activeProject) return emptySnapshot()
    if (method === 'session.search' && !this.user()) return []
    if (method === 'session.list' && !this.user()) return []
    if (method === 'plugin.list' && !this.user()) return []
    const local = this.account(), files = this.storage()
    switch (method) {
      case 'session.list': return local.sessions()
      case 'session.search': return local.sessions(params.query)
      case 'plugin.list': return this.plugins()
      case 'plugin.snapshot': {
        if (!this.catalog.plugins.some(plugin => plugin.id === params.plugin_id)) throw new Error('未知插件')
        return { plugin_id: params.plugin_id, state: { runs: local.activeProject ? this.saved(local.currentSession().id).runs.map(run => ({ ...run, records: run.records?.filter(record => record.plugin_id === params.plugin_id) })) : [] } }
      }
      case 'session.snapshot': { this.idle(); const value = await this.snapshot(); setImmediate(() => { this.emit({ event: 'ready', data: value }); this.notifyQuestion() }); return value }
      case 'project.create': this.idle(); if (!params.name.trim()) throw new Error('项目名称不能为空'); local.createProject(params.name); this.selectFiles(); this.emit({ event: 'ready', data: await this.snapshot() }); this.emit({ event: 'app.ready', data: this.state() }); return this.state()
      case 'project.select': this.idle(); local.selectProject(params.project_id); this.selectFiles(); this.emit({ event: 'ready', data: await this.snapshot() }); this.notifyQuestion(); this.emit({ event: 'app.ready', data: this.state() }); return this.state()
      case 'session.resume': this.idle(); local.resume(params.session_id); { const value = await this.snapshot(); setImmediate(() => this.notifyQuestion()); return value }
      case 'session.clear': this.idle(); local.createSession(); return this.snapshot()
      case 'session.rename': if (!params.title.trim()) throw new Error('会话标题不能为空'); local.rename(params.session_id, params.title); return local.sessions()
      case 'session.delete': {
        this.idle(); local.session(params.session_id); await this.configure()
        await this.backend.request('session.delete', { thread_id: params.session_id })
        const file = this.recordPath(params.session_id)
        local.deleteSession(params.session_id); rmSync(file, { force: true })
        this.emit({ event: 'ready', data: await this.snapshot() }); return local.sessions()
      }
      case 'session.export': { this.idle(); const value = await this.snapshot(params.session_id, false); return { text: value.messages.map(message => `${message.type === 'human' ? '用户' : '助手'}：\n${typeof message.data.content === 'string' ? message.data.content : JSON.stringify(message.data.content)}\n`).join('\n') } }
      case 'session.token_usage': if (!local.activeProject) throw new Error('请先选择项目'); await this.configure(); return this.backend.request('session.token_usage', { thread_id: local.currentSession().id })
      case 'run.start': case 'run.attachments': {
        this.idle(); if (!params.text.trim()) throw new Error('任务不能为空')
        const attachments = method === 'run.attachments' ? command.params.attachment_ids.map(id => files.attachment(id)) : []
        const session = local.currentSession(); await this.configure()
        const selected_plugins = params.selected_plugins ?? [], selected_skills = params.selected_skills ?? []
        if (selected_plugins.some(id => !this.plugins().some(plugin => plugin.id === id && plugin.enabled))) throw new Error('选中的插件未启用')
        if (selected_skills.some(id => !this.state().skills.some(skill => skill.id === id && skill.enabled))) throw new Error('选中的技能不可用')
        this.running = { id: command.id, thread: session.id, started: Date.now(), records: [], interrupts: [], answers: {} }
        try { await this.backend.request('run.start', { thread_id: session.id, run_id: command.id, text: params.text, selected_plugins, selected_skills, attachments: attachments.map(item => ({ name: item.name, media_type: item.media_type, path: files.resolveAttachment(item.id) })) }) }
        catch (error) { this.running = null; throw error }
        const saved = this.saved(session.id); saved.last_run_id = command.id; saved.attachment_ids = [...new Set([...saved.attachment_ids, ...attachments.map(item => item.id)])]; saved.active_run = { id: command.id, started: this.running.started, records: [] }; this.save(session.id, saved)
        local.touch(session.id, params.text); return { run_id: command.id }
      }
      case 'run.stop': if (!this.running || this.running.id !== params.run_id) throw new Error('当前任务不匹配'); await this.backend.request('run.stop', { run_id: params.run_id, thread_id: this.running.thread }); if (this.running?.interrupts.length) setImmediate(() => { void this.serial(() => this.finish('cancelled')).catch(error => this.emit({ event: 'protocol.error', data: { message: String(error) } })) }); return {}
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
        } else { if (!params.answer.trim()) throw new Error('答复不能为空'); answer = params.answer }
        const answers = { ...run.answers, [interrupt.id]: answer }
        if (!run.interrupts.every(item => item.id in answers)) { run.answers = answers; setImmediate(() => this.notifyQuestion()); return {} }
        await this.backend.request('run.resume', { thread_id: run.thread, run_id: run.id, decisions: answers })
        const approvedPlugins = run.interrupts.filter(item => item.value.kind === 'plugin_authorization' && (answers[item.id] as { approved?: boolean })?.approved === true).map(item => item.value.plugin_id).filter((id): id is string => typeof id === 'string')
        if (approvedPlugins.length) { files.authorizePlugins(approvedPlugins); this.emit({ event: 'app.ready', data: this.state() }) }
        run.interrupts = []; run.answers = {}; return { run_id: run.id }
      }
      case 'model.configure': this.idle(); if (!params.model_code.trim()) throw new Error('模型不能为空'); files.saveSettings({ model_code: params.model_code }); break
      case 'model.test': this.idle(); await this.configurePreferences(); await this.backend.request('model.test', {}); return this.state()
      case 'context.configure': { this.idle(); const max = Number(params.max_tokens); if (!Number.isSafeInteger(max) || max < 1) throw new Error('上下文窗口必须为正整数'); files.saveSettings({ max_context_tokens: max }); break }
      case 'runtime.configure': this.idle(); files.saveSettings({ runtimes: { python: params.python, node: params.node } }); break
      case 'permission.set': this.idle(); if (!['read_only', 'ask', 'full'].includes(params.mode)) throw new Error('权限模式无效'); files.saveSettings({ permission_mode: params.mode }); break
      case 'config.inspect': return files.inspectConfig(params.scope)
      case 'config.save': this.idle(); return files.saveConfig(params.scope, params.kind, params.content)
      case 'config.remove': this.idle(); return files.removeConfig(params.kind)
      case 'skill.read': return files.readSkill(params.scope, params.skill_id)
      case 'skill.save': this.idle(); return files.saveSkill(params.scope, params.skill_id, params.content)
      case 'skill.set': this.idle(); if (!['true', 'false'].includes(params.enabled)) throw new Error('技能状态无效'); return files.setSkill(params.scope, params.skill_id, params.enabled === 'true')
      case 'mcp.test': this.idle(); await this.configurePreferences(params.scope); return this.backend.request('mcp.test', { server_id: params.server_id })
      case 'plugin.set': { this.idle(); if (!this.catalog.plugins.some(plugin => plugin.id === params.plugin_id) || !['true', 'false'].includes(params.enabled)) throw new Error('无效插件设置'); files.saveSettings({ plugins: { ...files.readSettings().plugins, [params.plugin_id]: params.enabled === 'true' } }); await this.configurePreferences(); break }
      case 'usage.stats': return files.usageStats(params.period, params.model)
      default: throw new Error(`未实现的桌面命令：${method}`)
    }
    this.emit({ event: 'app.ready', data: this.state() }); return this.state()
  }
  receive(frame: EngineFrame): void {
    void this.serial(async () => {
      if (frame.event === 'backend.exited') { this.running = null; this.emit(frame); return }
      if (!this.running || this.running.id !== frame.data.run_id || this.running.thread !== frame.data.thread_id) return
      if (frame.event === 'run.cancelled') { await this.finish('cancelled'); return }
      const event = frame.data
      if (event.type === 'run.interrupted') {
        this.running.interrupts = event.data.interrupts as unknown as Interrupt[]
        const saved = this.saved(this.running.thread); saved.active_run = { id: this.running.id, started: this.running.started, records: this.running.records }; this.save(this.running.thread, saved)
        this.notifyQuestion(); return
      }
      if (event.type === 'run.completed' || event.type === 'run.failed') {
        if (event.type === 'run.failed') this.emit({ event: 'protocol.error', data: { message: String(event.data.message ?? event.data.error_type) } })
        await this.finish(event.type === 'run.completed' ? 'complete' : 'failed'); return
      }
      const record = this.convert(event)
      if (event.type === 'model.completed') {
        const usage = event.data.usage as { input_tokens: number; output_tokens: number; total_tokens: number } | undefined
        this.storage().recordUsage(this.account().activeProject!.id, this.running.thread, { request_id: event.data.message_id as string || event.operation_id, requested_at: event.data.requested_at as string || new Date().toISOString(), model: event.data.model as string || this.state().model!.model, source: 'model', usage: usage ?? null, cost_estimate: null })
        record.totals = this.totals(this.running.thread)
      }
      this.running.records.push(record)
      this.emit({ event: 'run.event', session_id: this.running.thread, run_id: this.running.id, data: record })
    }).catch(error => { this.emit({ event: 'protocol.error', data: { message: String(error) } }) })
  }
  private convert(event: EngineEvent): RunRecord {
    const kind: Record<string, string> = { 'model.delta': 'text', 'model.started': 'model_start', 'model.completed': 'model_end', 'tool.started': 'tool_start', 'tool.completed': 'tool_end', 'tool.failed': 'tool_end' }
    return { kind: event.type === 'model.delta' && event.agent_name && event.agent_name !== 'vertical-agent' ? 'model_delta' : kind[event.type] ?? event.type, name: event.name, seq: event.seq, operation_id: event.operation_id, parent_ids: event.parent_ids, node: event.node, agent_name: event.agent_name, ...(event.type.startsWith('tool.') ? { call_id: event.operation_id, status: event.type === 'tool.failed' ? 'failed' : event.type === 'tool.completed' ? 'success' : 'running' } : {}), ...event.data }
  }
  private async finish(status: 'complete' | 'failed' | 'cancelled'): Promise<void> {
    const run = this.running!
    const snapshot = await this.snapshot(run.thread, false)
    const saved = this.saved(run.thread)
    const records = run.records as Json[]
    const userMessage = [...snapshot.messages].reverse().find(message => message.type === 'human')
    const requests = new Map(run.records.filter(record => record.kind === 'model_start' || record.kind === 'model_end').map(record => [record.operation_id, record]))
    saved.runs.push({ id: run.id, started_at: new Date(run.started).toISOString(), finished_at: new Date().toISOString(), status, records: run.records, user_message_id: userMessage?.data.id as string | undefined, requests: [...requests.values()] as Json[], tools: Object.fromEntries(run.records.flatMap((record, i) => record.call_id ? [[record.call_id, records[i]]] : [])), elapsed: (Date.now() - run.started) / 1000 })
    delete saved.active_run
    this.save(run.thread, saved)
    snapshot.runs = saved.runs
    this.account().touch(run.thread)
    this.running = null
    this.emit({ event: 'run.finished', session_id: run.thread, run_id: run.id, data: { status, snapshot } })
  }
}
