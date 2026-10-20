import { afterEach, expect, it, vi } from 'vitest'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { DesktopApplication } from '../src/main/application'
import type { Backend, EngineEvent, Interrupt } from '../src/main/backend'
import type { Command, EventFrame } from '../src/shared/protocol'

const directories: string[] = []
const tokenReport = { max_tokens: 32000, used_tokens: 20, remaining_tokens: 31980, used_ratio: 20 / 32000, remaining_ratio: 31980 / 32000, sections: Object.fromEntries(['system_prompt', 'skills', 'mcp', 'memory', 'history'].map(name => [name, { name, tokens: name === 'history' ? 20 : 0, bytes: 0, ratio_of_used: name === 'history' ? 1 : 0, ratio_of_max: name === 'history' ? 20 / 32000 : 0, description: name }])), summary_text: '上下文估算 20 / 32000' }
afterEach(() => { for (const dir of directories.splice(0)) rmSync(dir, { recursive: true, force: true }) })
function setup() {
  const directory = mkdtempSync(path.join(tmpdir(), 'desktop-app-')); directories.push(directory)
  const events: EventFrame[] = [], snapshots = new Map<string, { messages: any[]; interrupts: Interrupt[]; run_id?: string }>()
  const request = vi.fn(async (method: string, params: any) => {
    if (method === 'environment') return { model: 'deepseek:deepseek-flash', analysis_enabled: true, exa_configured: true, python_executable: 'python', python_version: '3.12', node_executable: 'node', node_version: '22', platform: 'win32' }
    if (method === 'catalog.get') return { plugins: [{ id: 'research.citations', name: '引用核查', description: '核查引用', version: '1', workflows: [{ name: 'check_references', description: '核查参考文献' }] }], skills: [{ id: 'research.citations/reference-check', name: 'Reference check', description: '引用核查技能', source: 'plugin', plugin_id: 'research.citations' }] }
    if (method === 'snapshot') return { thread_id: params.thread_id, run_id: null, artifacts: [], token_usage: tokenReport, ...(snapshots.get(params.thread_id) ?? { messages: [], interrupts: [] }) }
    if (method === 'configure') return { model: params.options.model_code }
    if (method === 'run.start' || method === 'run.resume') return { run_id: params.run_id }
    if (method === 'model.test' || method === 'mcp.test' || method === 'run.stop' || method === 'session.delete') return {}
    throw new Error(`Unexpected bridge method: ${method}`)
  })
  const application = new DesktopApplication(directory, { request } as unknown as Backend, (frame: EventFrame) => events.push(frame))
  const command = (method: string, params = {}, id = randomUUID()) => application.serial(() => application.command({ id, method, params } as Command))
  return { application, command, request, snapshots, events, directory }
}
function event(run: string, thread: string, type: string, data = {}): EngineEvent { return { run_id: run, thread_id: thread, type, data, operation_id: 'call', name: 'test', agent_name: null } }

it('creates projects and restores isolated sessions from disk', async () => {
  const t = setup(); await t.application.initialize()
  await expect(t.command('project.create', { name: ' ' })).rejects.toThrow('名称')
  await t.command('project.create', { name: '研究项目' })
  const first = await t.command('session.snapshot') as any
  await t.command('session.rename', { session_id: first.session_id, title: '历史一' })
  const second = await t.command('session.clear') as any
  expect(second.session_id).not.toBe(first.session_id)
  expect(await t.command('session.search', { query: '历史' })).toHaveLength(1)
  await t.command('session.resume', { session_id: first.session_id })
  const restored = new DesktopApplication(t.directory, { request: t.request } as unknown as Backend, () => {})
  await restored.initialize()
  expect(await restored.command({ id: 'snapshot', method: 'session.snapshot', params: {} })).toMatchObject({ session_id: first.session_id })
  await t.command('session.delete', { session_id: second.session_id })
  expect(await t.command('session.list')).toHaveLength(1)
})

it('keeps source run IDs, validates attachments before start, streams and saves the final snapshot', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '项目' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await expect(s.command('run.attachments', { text: 'read', attachment_ids: ['foreign'] }, run)).rejects.toThrow('workspace')
  const file = path.join(s.directory, 'input.csv'); writeFileSync(file, 'x,y\n1,2\n')
  const [attachment] = s.application.storage().importAttachments([file])
  await s.command('run.attachments', { text: '读取数据', attachment_ids: [attachment.id] }, run)
  expect(s.request).toHaveBeenCalledWith('run.start', expect.objectContaining({ run_id: run, text: '读取数据', attachments: [expect.objectContaining({ name: 'input.csv' })] }))
  await expect(s.command('project.create', { name: '运行中' })).rejects.toThrow('任务')
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'model.delta', { text: '结果' }) })
  s.snapshots.set(snapshot.thread_id, { messages: [{ type: 'human', data: { id: 'user-1', content: '读取数据' } }, { type: 'ai', data: { id: 'model-1', content: '结果', usage_metadata: { input_tokens: 10, output_tokens: 2, total_tokens: 12 } } }], interrupts: [] })
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.completed') })
  await s.application.serial(async () => {})
  expect(s.events).toContainEqual(expect.objectContaining({ event: 'run.event', run_id: run, data: expect.objectContaining({ kind: 'text', text: '结果' }) }))
  expect(s.events).toContainEqual(expect.objectContaining({ event: 'run.finished', run_id: run, data: expect.objectContaining({ status: 'complete', snapshot: expect.objectContaining({ attachments: [attachment], totals: expect.objectContaining({ total_tokens: 12 }) }) }) }))
  const exported = await s.command('session.export', { session_id: snapshot.session_id }) as { text: string }
  expect(exported.text).toContain('结果')
})

it('resumes research questions with text and approvals with exact decisions using the same run ID', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '项目' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '分析' }, run)
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.interrupted', { interrupts: [{ id: 'research', value: { question: '分析目标？' } }] }) })
  await s.application.serial(async () => {})
  expect(s.events.at(-1)).toMatchObject({ event: 'interaction.request', data: { kind: 'question', question: '分析目标？' } })
  await s.command('interaction.answer', { run_id: run, interaction_id: 'research', answer: '比较两组均值' })
  expect(s.request).toHaveBeenCalledWith('run.resume', { thread_id: snapshot.thread_id, run_id: run, decisions: { research: '比较两组均值' } })
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.interrupted', { interrupts: [{ id: 'approval', value: { action_requests: [{ name: 'execute_python', args: { code: 'print(1)' } }] } }] }) })
  await s.application.serial(async () => {})
  expect(s.events.at(-1)).toMatchObject({ event: 'interaction.request', data: { kind: 'approval', action_requests: [{ name: 'execute_python', args: { code: 'print(1)' } }] } })
  expect((s.events.at(-1) as Extract<EventFrame, { event: 'interaction.request' }>).data.question).not.toContain('print(1)')
  await expect(s.command('interaction.answer', { run_id: run, interaction_id: 'approval', answer: 'anything' })).rejects.toThrow('审批')
  await s.command('interaction.answer', { run_id: run, interaction_id: 'approval', answer: 'reject' })
  expect(s.request).toHaveBeenCalledWith('run.resume', { thread_id: snapshot.thread_id, run_id: run, decisions: { approval: { decisions: [{ type: 'reject' }] } } })
})

it.each(['0', '475001', '1.5'])('rejects an invalid context budget %s before saving', async max => {
  const s = setup(); await s.application.initialize()
  expect(s.application.state().max_context_tokens).toBe(475000)
  await expect(s.command('context.configure', { max_tokens: max })).rejects.toThrow('475,000')
  expect(s.application.state().max_context_tokens).toBe(475000)
})

it('persists settings and uses the backend plugin catalog across projects', async () => {
  const s = setup(); await s.application.initialize()
  await s.command('model.configure', { model_code: 'deepseek-flash' })
  await s.command('context.configure', { max_tokens: '32000' })
  await s.command('permission.set', { mode: 'read_only' })
  expect(s.application.state().plugins).toEqual([expect.objectContaining({ id: 'research.citations', enabled: false })])
  await expect(s.command('plugin.set', { plugin_id: 'not-installed', enabled: 'true' })).rejects.toThrow('插件')
  await s.command('plugin.set', { plugin_id: 'research.citations', enabled: 'true' })
  expect(s.application.state().plugins[0]).toMatchObject({ enabled: true, loaded: true })
  expect(s.application.state().skills[0]).toMatchObject({ plugin_id: 'research.citations', enabled: true })
  await s.command('model.test')
  await s.command('project.create', { name: '新项目' })
  expect(s.application.state()).toMatchObject({ max_context_tokens: 32000, permission_mode: 'read_only' })
  expect(await s.command('plugin.snapshot', { plugin_id: 'research.citations' })).toEqual({ plugin_id: 'research.citations', state: { runs: [] } })
  expect(s.request.mock.calls.filter(([method]) => method === 'configure').at(-1)![1].options.enabled_plugins).toEqual(['research.citations'])
  expect(await s.command('session.snapshot')).toMatchObject({ token_usage: tokenReport })
})

it('stops the exact running checkpoint thread', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '项目' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '执行任务' }, run)
  await s.command('run.stop', { run_id: run })
  expect(s.request).toHaveBeenCalledWith('run.stop', { run_id: run, thread_id: snapshot.thread_id })
})

it('labels imported global and project skills separately when configuring the backend', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '项目' })
  for (const scope of ['global', 'project'] as const) {
    const id = `${scope}-skill`, source = path.join(s.directory, id)
    mkdirSync(source)
    writeFileSync(path.join(source, 'SKILL.md'), `---\nname: ${id}\n---\n${scope} instructions`)
    s.application.storage().importSkill(scope, source)
  }
  await s.command('app.snapshot')
  expect(s.request.mock.calls.filter(([method]) => method === 'configure').at(-1)![1].options.skills).toEqual([
    expect.objectContaining({ id: 'project-skill', source: '项目' }),
    expect.objectContaining({ id: 'global-skill', source: '全局' })
  ])
  await s.command('skill.set', { scope: 'project', skill_id: 'project-skill', enabled: 'false' })
  await s.command('app.snapshot')
  expect(s.request.mock.calls.filter(([method]) => method === 'configure').at(-1)![1].options.skills).toEqual([
    expect.objectContaining({ id: 'global-skill', source: '全局' })
  ])
})

it('restores already published artifacts only for the current session runs', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '项目' })
  const first = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '生成报告' }, run)
  const file = path.join(s.application.workspace(), 'result.csv'); writeFileSync(file, 'value\n1')
  const artifact = s.application.storage().publishArtifact('/workspace/result.csv', run)
  s.application.receive({ event: 'agent.event', data: event(run, first.thread_id, 'run.completed') })
  await s.application.serial(async () => {})
  expect(await s.command('session.snapshot')).toMatchObject({ artifacts: [artifact] })
  expect(await s.command('session.clear')).toMatchObject({ artifacts: [] })
  expect(await s.command('session.resume', { session_id: first.session_id })).toMatchObject({ artifacts: [artifact] })
})

it('keeps the running command addressable if the terminal snapshot fails', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '项目' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '执行任务' }, run)
  const original = s.request.getMockImplementation()!
  let rejectSnapshot = true
  s.request.mockImplementation(async (method: string, params: any) => {
    if (method === 'snapshot' && rejectSnapshot) throw new Error('snapshot unavailable')
    return original(method, params)
  })
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.completed') })
  await s.application.serial(async () => {})
  expect(s.events).toContainEqual({ event: 'protocol.error', data: { message: 'Error: snapshot unavailable' } })
  rejectSnapshot = false
  await s.command('run.stop', { run_id: run })
  s.application.receive({ event: 'run.cancelled', data: { run_id: run, thread_id: snapshot.thread_id } })
  await s.application.serial(async () => {})
  expect(s.events.at(-1)).toMatchObject({ event: 'run.finished', data: { status: 'cancelled', snapshot: { runs: [expect.anything()] } } })
})

it.each(['model.started', 'tool.started'])('saves an in-flight %s run once when the backend exits without making RPC requests', async type => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '崩溃恢复' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '执行任务' }, run)
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, type) })
  await s.application.serial(async () => {})
  const original = s.request.getMockImplementation()!
  s.request.mockClear()
  s.request.mockImplementation(async () => { throw new Error('Backend is unavailable') })
  const exited = { event: 'backend.exited' as const, data: { message: 'Python exited' } }
  s.application.receive(exited)
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.completed') })
  s.application.receive(exited)
  await s.application.serial(async () => {})
  expect(s.request).not.toHaveBeenCalled()
  expect(s.events.filter(frame => frame.event === 'protocol.error')).toEqual([])
  expect(s.events.at(-1)).toEqual(exited)

  s.request.mockImplementation(original)
  const restored = new DesktopApplication(s.directory, { request: s.request } as unknown as Backend, () => {})
  await restored.initialize()
  const result = await restored.command({ id: 'snapshot', method: 'session.snapshot', params: {} }) as any
  expect(result).toMatchObject({ session_id: snapshot.session_id, runs: [{ id: run, status: 'failed', records: [expect.objectContaining({ operation_id: 'call' })] }] })
  expect(result.runs).toHaveLength(1)
  expect(result.runs[0].started_at).toEqual(expect.any(String))
  expect(result.runs[0].finished_at).toEqual(expect.any(String))
  expect(result.runs[0].requests).toHaveLength(type === 'model.started' ? 1 : 0)
  expect(Object.keys(result.runs[0].tools)).toHaveLength(type === 'tool.started' ? 1 : 0)
  expect(result.active_run).toBeUndefined()
  expect((await restored.command({ id: 'again', method: 'session.snapshot', params: {} }) as any).runs).toEqual(result.runs)
  expect(s.request.mock.calls.some(([method]) => method === 'run.start' || method === 'run.resume')).toBe(false)
})

it('does not create a failed run when an idle backend exits', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '空闲' })
  s.request.mockClear()
  s.application.receive({ event: 'backend.exited', data: { message: 'Python exited' } })
  await s.application.serial(async () => {})
  expect(s.request).not.toHaveBeenCalled()
  expect(await s.command('session.snapshot')).toMatchObject({ runs: [] })
})

it('settles the current session unfinished run after application restart without replaying tools', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '重启' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '执行任务' }, run)
  s.request.mockClear()
  const restored = new DesktopApplication(s.directory, { request: s.request } as unknown as Backend, () => {})
  await restored.initialize()
  const result = await restored.command({ id: 'snapshot', method: 'session.snapshot', params: {} }) as any
  expect(result).toMatchObject({ session_id: snapshot.session_id, runs: [{ id: run, status: 'failed', records: [] }] })
  expect(result.runs).toHaveLength(1)
  expect(result.active_run).toBeUndefined()
  expect((await restored.command({ id: 'again', method: 'session.snapshot', params: {} }) as any).runs).toEqual(result.runs)
  expect(s.request.mock.calls.some(([method]) => method === 'run.start' || method === 'run.resume')).toBe(false)
})

it('tests MCP using the selected global or project configuration', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: 'MCP' })
  await s.command('config.save', { scope: 'global', kind: 'mcp', content: JSON.stringify({ mcpServers: { global: { transport: 'stdio', command: 'node' } } }) })
  await s.command('config.save', { scope: 'project', kind: 'mcp', content: JSON.stringify({ mcpServers: { project: { transport: 'stdio', command: 'node' } } }) })
  await s.command('mcp.test', { scope: 'global', server_id: 'global' })
  expect(s.request.mock.calls.filter(([method]) => method === 'configure').at(-1)![1].options.mcp_servers).toHaveProperty('global')
  expect(s.request.mock.calls.filter(([method]) => method === 'configure').at(-1)![1].options.mcp_servers).not.toHaveProperty('project')
  await s.command('mcp.test', { scope: 'project', server_id: 'project' })
  expect(s.request.mock.calls.filter(([method]) => method === 'configure').at(-1)![1].options.mcp_servers).toHaveProperty('project')
})

it('passes explicit selections and resumes a structured plugin approval only for the active run', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '插件' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await expect(s.command('run.start', { text: '核查', selected_plugins: ['research.citations'] }, run)).rejects.toThrow('未启用')
  await s.command('plugin.set', { plugin_id: 'research.citations', enabled: 'true' })
  await s.command('run.start', { text: '核查', selected_plugins: ['research.citations'], selected_skills: ['research.citations/reference-check'] }, run)
  expect(s.request).toHaveBeenCalledWith('run.start', expect.objectContaining({ selected_plugins: ['research.citations'], selected_skills: ['research.citations/reference-check'] }))
  const value = { kind: 'plugin_authorization', plugin_id: 'research.citations', request_id: 'req-1', workflow_name: 'check_references', question: '允许核查？', purpose: '核查引用', input_scope: ['references.txt'], planned_outputs: ['report.md'], allowed_effects: ['write'] }
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.interrupted', { interrupts: [{ id: 'approval-1', value }] }) })
  await s.application.serial(async () => {})
  expect(s.events.at(-1)).toMatchObject({ event: 'interaction.request', run_id: run, data: { ...value, interaction_id: 'approval-1' } })
  await expect(s.command('interaction.answer', { run_id: 'stale-run', interaction_id: 'approval-1', answer: 'approve' })).rejects.toThrow('失效')
  await s.command('interaction.answer', { run_id: run, interaction_id: 'approval-1', answer: 'approve' })
  expect(s.request).toHaveBeenCalledWith('run.resume', { thread_id: snapshot.thread_id, run_id: run, decisions: { 'approval-1': { request_id: 'req-1', approved: true } } })
  expect(s.application.storage().authorizedPlugins()).toEqual(['research.citations'])
  const restored = new DesktopApplication(s.directory, { request: s.request } as unknown as Backend, () => {})
  await restored.initialize()
  expect(s.request.mock.calls.filter(([method]) => method === 'configure').at(-1)![1].options.authorized_plugins).toEqual(['research.citations'])
  await expect(s.command('interaction.answer', { run_id: run, interaction_id: 'approval-1', answer: 'approve' })).rejects.toThrow('失效')
})

it('delivers question options and custom text without granting plugin permissions', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '选项' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '生成报告' }, run)
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.interrupted', { interrupts: [{ id: 'choose', value: { kind: 'question', question: '选择格式', options: ['摘要', '完整报告'] } }] }) })
  await s.application.serial(async () => {})
  expect(s.events.at(-1)).toMatchObject({ event: 'interaction.request', data: { options: ['摘要', '完整报告'], kind: 'question' } })
  await s.command('interaction.answer', { run_id: run, interaction_id: 'choose', answer: '自定义：双语摘要' })
  expect(s.request).toHaveBeenCalledWith('run.resume', { thread_id: snapshot.thread_id, run_id: run, decisions: { choose: '自定义：双语摘要' } })
  expect(s.application.storage().authorizedPlugins()).toEqual([])
})

it('does not remember rejected or unsuccessfully submitted plugin approvals', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '审批' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '核查' }, run)
  const value = { kind: 'plugin_authorization', plugin_id: 'research.citations', request_id: 'req-1', question: '允许？' }
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.interrupted', { interrupts: [{ id: 'approval', value }] }) })
  await s.application.serial(async () => {})
  s.request.mockRejectedValueOnce(new Error('resume failed'))
  await expect(s.command('interaction.answer', { run_id: run, interaction_id: 'approval', answer: 'approve' })).rejects.toThrow('resume failed')
  expect(s.application.storage().authorizedPlugins()).toEqual([])
  await s.command('interaction.answer', { run_id: run, interaction_id: 'approval', answer: 'reject' })
  expect(s.application.storage().authorizedPlugins()).toEqual([])
})

it('retains workflow metadata and counts plugin model usage once in the durable timeline', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '流程' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '核查' }, run)
  s.application.receive({ event: 'agent.event', data: event(run, 'foreign-thread', 'model.delta', { text: '其他会话' }) })
  s.application.receive({ event: 'agent.event', data: { ...event(run, snapshot.thread_id, 'model.delta', { text: '插件步骤结果' }), agent_name: 'check_references' } })
  s.application.receive({ event: 'agent.event', data: { ...event(run, snapshot.thread_id, 'custom.progress', { kind: 'workflow.node', plugin_id: 'research.citations', workflow_name: 'check_references', node: 'fetch', status: 'running', summary: '读取引用' }), seq: 2, parent_ids: ['dispatch-1'], operation_id: 'node-1', node: 'fetch', agent_name: 'research.citations' } })
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'model.completed', { message_id: 'plugin-model-1', model: 'plugin-model', usage: { input_tokens: 4, output_tokens: 2, total_tokens: 6 } }) })
  s.application.receive({ event: 'agent.event', data: { ...event(run, snapshot.thread_id, 'model.started'), operation_id: 'main-op' } })
  s.application.receive({ event: 'agent.event', data: { ...event(run, snapshot.thread_id, 'model.completed', { message_id: 'main-model-1', model: 'main-model', usage: { input_tokens: 10, output_tokens: 2, total_tokens: 12 } }), operation_id: 'main-op' } })
  s.snapshots.set(snapshot.thread_id, { messages: [{ type: 'human', data: { id: 'human-1', content: '核查' } }, { type: 'ai', data: { id: 'main-model-1', content: '完成', usage_metadata: { input_tokens: 10, output_tokens: 2, total_tokens: 12 } } }], interrupts: [] })
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.completed') })
  await s.application.serial(async () => {})
  const restored = await s.command('session.snapshot') as any
  expect(restored.totals).toMatchObject({ requests: 2, total_tokens: 18, models: [expect.objectContaining({ cache_hit_tokens: null }), expect.objectContaining({ cache_hit_tokens: null })] })
  expect(restored.runs[0]).toMatchObject({ id: run, status: 'complete', user_message_id: 'human-1', records: [expect.objectContaining({ kind: 'model_delta', text: '插件步骤结果' }), expect.objectContaining({ kind: 'workflow.node', seq: 2, parent_ids: ['dispatch-1'], node: 'fetch', agent_name: 'research.citations' }), expect.anything(), expect.anything(), expect.anything()] })
  expect(restored.runs[0].requests).toHaveLength(2)
  expect(s.events.some(frame => frame.event === 'run.event' && frame.data.text === '其他会话')).toBe(false)
  expect((await s.command('usage.stats', { period: '', model: '' }) as any).totals.total_tokens).toBe(18)
})

it.each([true, false])('restores a durable paused run after backend exit when interrupt notification was delivered: %s', async delivered => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '恢复' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '继续' }, run)
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'custom.progress', { kind: 'workflow.node', plugin_id: 'research.citations', node: 'review', status: 'running' }) })
  const interrupts = [{ id: 'question-1', value: { question: '补充说明？' } }]
  if (delivered) s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.interrupted', { interrupts }) })
  await s.application.serial(async () => {})
  s.snapshots.set(snapshot.thread_id, { messages: [], interrupts, run_id: run })
  s.request.mockClear()
  s.application.receive({ event: 'backend.exited', data: { message: 'Python exited while paused' } })
  await s.application.serial(async () => {})
  expect(s.request).not.toHaveBeenCalled()
  const restored = new DesktopApplication(s.directory, { request: s.request } as unknown as Backend, () => {})
  await restored.initialize()
  expect(await restored.command({ id: 'snapshot', method: 'session.snapshot', params: {} })).toMatchObject({ runs: [], active_run: { id: run, records: [expect.objectContaining({ kind: 'workflow.node' })] } })
  await restored.command({ id: 'answer', method: 'interaction.answer', params: { run_id: run, interaction_id: 'question-1', answer: '说明' } })
  expect(s.request).toHaveBeenCalledWith('run.resume', { thread_id: snapshot.thread_id, run_id: run, decisions: { 'question-1': '说明' } })
  s.snapshots.set(snapshot.thread_id, { messages: [], interrupts: [], run_id: run })
  restored.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.completed') })
  await restored.serial(async () => {})
  const completed = await restored.command({ id: 'completed', method: 'session.snapshot', params: {} }) as any
  expect(completed.runs).toHaveLength(1)
  expect(completed.runs[0]).toMatchObject({ id: run, status: 'complete', records: [expect.objectContaining({ kind: 'workflow.node' })] })
  expect(completed.active_run).toBeUndefined()
})
