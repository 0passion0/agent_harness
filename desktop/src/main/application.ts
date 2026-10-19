import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import path from 'node:path'
import type { ApplicationState, AppPlugin, Command, ConfigScope, EventFrame, Json, Question, RunRecord, Snapshot, UserProfile } from '../shared/protocol'
import type { Backend, EngineFrame, EngineEvent, Interrupt } from './backend'
import { DesktopStore } from './desktop-store'
import { LocalStore, writeAtomic } from './local-store'

type Environment = { model: string; exa_configured: boolean; analysis_enabled: boolean; python_executable: string; node_executable: string; python_version: string; node_version: string; platform: string }
type Saved = { runs: Snapshot['runs']; attachment_ids: string[]; analysis: Record<string, Json>; last_run_id: string | null }
type Running = { id: string; thread: string; started: number; records: RunRecord[]; interrupts: Interrupt[]; answers: Record<string, Json> }
type SnapshotResult = { thread_id: string; messages: Snapshot['messages']; interrupts: Interrupt[]; analysis: Record<string, Json> | null; artifacts: string[] }
const emptySnapshot = (): Snapshot => ({ session_id: '', thread_id: '', thread_ids: [], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false })

export class DesktopApplication {
  private local: LocalStore | null = null
  private files: DesktopStore | null = null
  private environment: Environment | null = null
  private running: Running | null = null
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
    return this.state()
  }
  async accountChanged(): Promise<ApplicationState> {
    this.idle()
    this.local = null; this.files = null
    if (this.user()) { this.local = new LocalStore(this.directory, Number(this.user()!.userID)); this.selectFiles() }
    const state = this.state()
    this.emit({ event: 'app.ready', data: state })
    this.emit({ event: 'ready', data: this.local?.activeProject ? await this.snapshot() : emptySnapshot() })
    return state
  }
  private plugins(): AppPlugin[] {
    const settings = this.files?.readSettings()
    const enabled = settings?.plugins ?? {}
    return [
      { id: 'user_config', name: '用户配置', description: '模型、指令和工具设置', interaction_kinds: [], enabled: true, configured: true, loaded: true },
      { id: 'data_analysis', name: '科研数据分析', description: '后端能力暂未接入，本次仅保留原版界面与配置', interaction_kinds: ['research_question'], enabled: enabled.data_analysis ?? false, configured: false, loaded: false },
      { id: 'exa', name: 'Exa 联网检索', description: '后端能力暂未接入，本次仅保留原版界面与配置', interaction_kinds: [], enabled: enabled.exa ?? false, configured: false, loaded: false }
    ]
  }
  state(): ApplicationState {
    const settings = this.files?.readSettings()
    const model = settings?.model_code || this.environment?.model
    return {
      build_mode: 'development', user: this.user(), projects: this.local?.projects ?? [], active_project_id: this.local?.activeProject?.id ?? null,
      model: model ? { provider: model.startsWith('deepseek:') ? 'deepseek' : 'harness', model: model.replace(/^deepseek:/, '') } : null,
      exa: { configured: false }, plugins: this.plugins(),
      runtimes: settings?.runtimes ?? { python: '', node: '' }, permission_mode: settings?.permission_mode ?? 'ask',
      data_dir: this.directory, max_context_tokens: settings?.max_context_tokens ?? 128000
    }
  }
  private async configure(): Promise<void> {
    const local = this.account(), files = this.storage(), settings = files.readSettings()
    const config = files.inspectConfig('project')
    const skills = config.skills.filter(skill => skill.enabled).map(skill => ({ id: skill.id, content: files.readSkill('project', skill.id).content }))
    await this.backend.request('configure', {
      tenant_id: local.activeProject!.id, user_id: String(this.user()!.userID), workspace_root: this.workspace(), runtime_data_dir: local.projectDirectory(),
      options: { model_code: settings.model_code || this.environment!.model, exa_api_key: this.plugins().find(plugin => plugin.id === 'exa')!.enabled ? settings.exa_api_key : '',
        runtimes: settings.runtimes, permission_mode: settings.permission_mode, max_context_tokens: settings.max_context_tokens,
        agent_content: config.agent.content, skills, mcp_servers: config.mcp.servers,
        analysis_enabled: false,
        analysis_plugin_dir: files.pluginDirectory('data_analysis'),
        exa_enabled: false }
    })
  }
  private async configurePreferences(scope: ConfigScope = 'project'): Promise<void> {
    if (scope === 'project' && this.account().activeProject) { await this.configure(); return }
    const files = this.storage(), settings = files.readSettings(), config = files.inspectConfig('global')
    await this.backend.request('configure', {
      tenant_id: 'preferences', user_id: String(this.user()!.userID), workspace_root: files.workspace, runtime_data_dir: files.directory,
      options: { model_code: settings.model_code || this.environment!.model, exa_api_key: settings.exa_api_key, runtimes: settings.runtimes,
        permission_mode: settings.permission_mode, max_context_tokens: settings.max_context_tokens, agent_content: config.agent.content,
        skills: config.skills.filter(item => item.enabled).map(item => ({ id: item.id, content: files.readSkill('global', item.id).content })),
        mcp_servers: config.mcp.servers, analysis_enabled: false,
        analysis_plugin_dir: files.pluginDirectory('data_analysis'),
        exa_enabled: false }
    })
  }
  private recordPath(session: string): string { this.account().session(session); return path.join(this.account().projectDirectory(), 'sessions', `${session}.json`) }
  private saved(session: string): Saved {
    const file = this.recordPath(session)
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) as Saved : { runs: [], attachment_ids: [], analysis: {}, last_run_id: null }
  }
  private save(session: string, value: Saved): void { writeAtomic(this.recordPath(session), JSON.stringify(value)) }
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
      if (message.data.id) this.storage().recordUsage(local.activeProject!.id, session.id, { request_id: message.data.id as string, requested_at: new Date().toISOString(), model: metadata?.model_name as string || this.state().model!.model, source: 'model', usage: usage ?? null, cost_estimate: null })
    }
    const artifacts = result.artifacts.map(file => {
      const relative = `/workspace/${file}`
      const existing = this.storage().listArtifacts().find(item => item.relative_path === relative)
      if (existing) return existing
      if (!saved.last_run_id) throw new Error('报告缺少对应的任务记录')
      return this.storage().publishArtifact(relative, saved.last_run_id)
    })
    saved.analysis = result.analysis ?? {}
    if (saved.analysis.report_open_path && saved.analysis.analysis_report_path) saved.analysis.report_open_path = `/workspace/${saved.analysis.analysis_report_path}`
    this.save(session.id, saved)
    const usages = result.messages.filter(message => message.type === 'ai').map(message => message.data.usage_metadata).filter(Boolean) as Record<string, number>[]
    const totals = { total_tokens: usages.reduce((sum, item) => sum + item.total_tokens, 0), input_tokens: usages.reduce((sum, item) => sum + item.input_tokens, 0), output_tokens: usages.reduce((sum, item) => sum + item.output_tokens, 0), requests: result.messages.filter(message => message.type === 'ai').length, complete: usages.length === result.messages.filter(message => message.type === 'ai').length }
    const snapshot: Snapshot = { session_id: session.id, thread_id: session.id, thread_ids: [session.id], messages: result.messages, attachments: saved.attachment_ids.map(id => this.storage().attachment(id)), artifacts, runs: saved.runs, totals, unsettled_history: result.interrupts.length > 0 }
    if (restoreInterrupts && result.interrupts.length && !this.running) this.running = { id: randomUUID(), thread: session.id, started: Date.now(), records: [], interrupts: result.interrupts, answers: {} }
    return snapshot
  }
  private question(): Question {
    const item = this.running!.interrupts.find(interrupt => !(interrupt.id in this.running!.answers))!
    const actions = item.value.action_requests
    return { interaction_id: item.id, plugin_id: actions ? 'harness' : 'data_analysis', kind: actions ? 'approval' : 'research_question', question: actions ? actions.map(action => `${action.name}\n${JSON.stringify(action.args, null, 2)}`).join('\n\n') : String(item.value.question) }
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
    if (method === 'app.snapshot') return this.state()
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
      case 'plugin.snapshot': return { plugin_id: params.plugin_id, state: params.plugin_id === 'data_analysis' && local.activeProject ? this.saved(local.currentSession().id).analysis : {} }
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
        const saved = this.saved(session.id); saved.last_run_id = command.id; saved.attachment_ids = [...new Set([...saved.attachment_ids, ...attachments.map(item => item.id)])]; this.save(session.id, saved)
        this.running = { id: command.id, thread: session.id, started: Date.now(), records: [], interrupts: [], answers: {} }
        try { await this.backend.request('run.start', { thread_id: session.id, run_id: command.id, text: params.text, attachments: attachments.map(item => ({ name: item.name, media_type: item.media_type, path: files.resolveAttachment(item.id) })) }) }
        catch (error) { this.running = null; throw error }
        local.touch(session.id, params.text); return { run_id: command.id }
      }
      case 'run.stop': if (!this.running || this.running.id !== params.run_id) throw new Error('当前任务不匹配'); await this.backend.request('run.stop', { run_id: params.run_id, thread_id: this.running.thread }); if (this.running?.interrupts.length) setImmediate(() => { void this.serial(() => this.finish('cancelled')).catch(error => this.emit({ event: 'protocol.error', data: { message: String(error) } })) }); return {}
      case 'interaction.answer': {
        const run = this.running
        if (!run || run.id !== params.run_id || this.question().interaction_id !== params.interaction_id) throw new Error('交互请求已失效')
        const interrupt = run.interrupts.find(item => item.id === params.interaction_id)!
        if (interrupt.value.action_requests) {
          if (!['approve', 'reject'].includes(params.answer)) throw new Error('无效审批决定')
          run.answers[interrupt.id] = { decisions: interrupt.value.action_requests.map(() => ({ type: params.answer })) }
        } else { if (!params.answer.trim()) throw new Error('答复不能为空'); run.answers[interrupt.id] = params.answer }
        if (!run.interrupts.every(item => item.id in run.answers)) { setImmediate(() => this.notifyQuestion()); return {} }
        await this.backend.request('run.resume', { thread_id: run.thread, run_id: run.id, decisions: run.answers }); run.interrupts = []; run.answers = {}; return { run_id: run.id }
      }
      case 'model.configure': this.idle(); if (!params.model_code.trim()) throw new Error('模型不能为空'); files.saveSettings({ model_code: params.model_code }); break
      case 'model.test': this.idle(); await this.configurePreferences(); await this.backend.request('model.test', {}); return this.state()
      case 'context.configure': { this.idle(); const max = Number(params.max_tokens); if (!Number.isSafeInteger(max) || max < 1) throw new Error('上下文窗口必须为正整数'); files.saveSettings({ max_context_tokens: max }); break }
      case 'exa.configure': this.idle(); if (!params.api_key.trim()) throw new Error('密钥不能为空'); files.saveSettings({ exa_api_key: params.api_key, plugins: { ...files.readSettings().plugins, exa: true } }); break
      case 'exa.clear': this.idle(); files.saveSettings({ exa_api_key: '', plugins: { ...files.readSettings().plugins, exa: false } }); break
      case 'runtime.configure': this.idle(); files.saveSettings({ runtimes: { python: params.python, node: params.node } }); break
      case 'permission.set': this.idle(); if (!['read_only', 'ask', 'full'].includes(params.mode)) throw new Error('权限模式无效'); files.saveSettings({ permission_mode: params.mode }); break
      case 'config.inspect': return files.inspectConfig(params.scope)
      case 'config.save': this.idle(); return files.saveConfig(params.scope, params.kind, params.content)
      case 'config.remove': this.idle(); return files.removeConfig(params.kind)
      case 'skill.read': return files.readSkill(params.scope, params.skill_id)
      case 'skill.save': this.idle(); return files.saveSkill(params.scope, params.skill_id, params.content)
      case 'skill.set': this.idle(); if (!['true', 'false'].includes(params.enabled)) throw new Error('技能状态无效'); return files.setSkill(params.scope, params.skill_id, params.enabled === 'true')
      case 'mcp.test': this.idle(); await this.configurePreferences(params.scope); return this.backend.request('mcp.test', { server_id: params.server_id })
      case 'plugin.set': { this.idle(); if (!['data_analysis', 'exa'].includes(params.plugin_id) || !['true', 'false'].includes(params.enabled)) throw new Error('无效插件设置'); files.saveSettings({ plugins: { ...files.readSettings().plugins, [params.plugin_id]: params.enabled === 'true' } }); break }
      case 'plugin.files': return files.pluginFiles(params.plugin_id)
      case 'plugin.read': return files.readPlugin(params.plugin_id, params.path)
      case 'plugin.save': this.idle(); return files.savePlugin(params.plugin_id, params.path, params.content)
      case 'plugin.export': return files.exportPlugin(params.plugin_id)
      case 'usage.stats': return files.usageStats(params.period, params.model)
      default: throw new Error(`未实现的桌面命令：${method}`)
    }
    this.emit({ event: 'app.ready', data: this.state() }); return this.state()
  }
  receive(frame: EngineFrame): void {
    void this.serial(async () => {
      if (frame.event === 'backend.exited') { this.running = null; this.emit(frame); return }
      if (!this.running || this.running.id !== frame.data.run_id) return
      if (frame.event === 'run.cancelled') { await this.finish('cancelled'); return }
      const event = frame.data
      if (event.type === 'run.interrupted') { this.running.interrupts = event.data.interrupts as unknown as Interrupt[]; this.notifyQuestion(); return }
      if (event.type === 'run.completed' || event.type === 'run.failed') {
        if (event.type === 'run.failed') this.emit({ event: 'protocol.error', data: { message: String(event.data.message ?? event.data.error_type) } })
        await this.finish(event.type === 'run.completed' ? 'complete' : 'failed'); return
      }
      const record = this.convert(event)
      this.running.records.push(record)
      this.emit({ event: 'run.event', session_id: this.running.thread, run_id: this.running.id, data: record })
    }).catch(error => { this.emit({ event: 'protocol.error', data: { message: String(error) } }) })
  }
  private convert(event: EngineEvent): RunRecord {
    const kind: Record<string, string> = { 'model.delta': 'text', 'model.started': 'model_start', 'model.completed': 'model_end', 'tool.started': 'tool_start', 'tool.completed': 'tool_end', 'tool.failed': 'tool_end' }
    return { kind: kind[event.type] ?? event.type, name: event.name, ...(event.type.startsWith('tool.') ? { call_id: event.operation_id, status: event.type === 'tool.failed' ? 'failed' : event.type === 'tool.completed' ? 'success' : 'running' } : {}), ...event.data }
  }
  private async finish(status: 'complete' | 'failed' | 'cancelled'): Promise<void> {
    const run = this.running!
    const snapshot = await this.snapshot(run.thread, false)
    const saved = this.saved(run.thread)
    const records = run.records as Json[]
    saved.runs.push({ requests: records.filter((_, i) => run.records[i].kind.startsWith('model_')), tools: Object.fromEntries(run.records.flatMap((record, i) => record.call_id ? [[record.call_id, records[i]]] : [])), elapsed: (Date.now() - run.started) / 1000 })
    this.save(run.thread, saved)
    snapshot.runs = saved.runs
    this.account().touch(run.thread)
    this.running = null
    this.emit({ event: 'run.finished', session_id: run.thread, run_id: run.id, data: { status, snapshot } })
  }
}
