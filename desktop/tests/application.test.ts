import { afterEach, expect, it, vi } from 'vitest'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { DesktopApplication } from '../src/main/application'
import type { Backend, EngineEvent, Interrupt } from '../src/main/backend'
import type { Command, EventFrame, UserProfile } from '../src/shared/protocol'

const directories: string[] = []
afterEach(() => { for (const dir of directories.splice(0)) rmSync(dir, { recursive: true, force: true }) })
function setup() {
  const directory = mkdtempSync(path.join(tmpdir(), 'desktop-app-')); directories.push(directory)
  const events: EventFrame[] = [], snapshots = new Map<string, { messages: any[]; interrupts: Interrupt[] }>()
  const request = vi.fn(async (method: string, params: any) => {
    if (method === 'environment') return { model: 'deepseek:deepseek-flash', analysis_enabled: true, exa_configured: true, python_executable: 'python', python_version: '3.12', node_executable: 'node', node_version: '22', platform: 'win32' }
    if (method === 'snapshot') return { thread_id: params.thread_id, analysis: null, artifacts: [], ...(snapshots.get(params.thread_id) ?? { messages: [], interrupts: [] }) }
    if (method === 'configure') return { model: params.options.model_code }
    if (method === 'run.start' || method === 'run.resume') return { run_id: params.run_id }
    if (method === 'model.test' || method === 'mcp.test' || method === 'run.stop' || method === 'session.delete') return {}
    throw new Error(`Unexpected bridge method: ${method}`)
  })
  let user: UserProfile | null = { userID: 7, realName: 'test', userName: 'test', userAvatar: '', userPhone: '', userCredit: 0, isCo: false, isBeta: false }
  const application = new DesktopApplication(directory, { request } as unknown as Backend, () => user, frame => events.push(frame))
  const command = (method: string, params = {}, id = randomUUID()) => application.serial(() => application.command({ id, method, params } as Command))
  return { application, command, request, snapshots, events, directory, logout: () => { user = null } }
}
function event(run: string, thread: string, type: string, data = {}): EngineEvent { return { run_id: run, thread_id: thread, type, data, operation_id: 'call', name: 'test', agent_name: null } }

it('boots without login, then creates projects and restores isolated sessions from disk', async () => {
  const s = setup(); s.logout(); await s.application.initialize()
  expect(await s.command('session.snapshot')).toMatchObject({ messages: [], session_id: '' })
  expect(await s.command('session.search', { query: '' })).toEqual([])
  await expect(s.command('project.create', { name: 'x' })).rejects.toThrow('登录')
  const t = setup(); await t.application.initialize()
  await expect(t.command('project.create', { name: ' ' })).rejects.toThrow('名称')
  await t.command('project.create', { name: '研究项目' })
  const first = await t.command('session.snapshot') as any
  await t.command('session.rename', { session_id: first.session_id, title: '历史一' })
  const second = await t.command('session.clear') as any
  expect(second.session_id).not.toBe(first.session_id)
  expect(await t.command('session.search', { query: '历史' })).toHaveLength(1)
  await t.command('session.resume', { session_id: first.session_id })
  const restored = new DesktopApplication(t.directory, { request: t.request } as unknown as Backend, () => t.application.state().user, () => {})
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
  s.snapshots.set(snapshot.thread_id, { messages: [{ type: 'human', data: { content: '读取数据' } }, { type: 'ai', data: { content: '结果', usage_metadata: { input_tokens: 10, output_tokens: 2, total_tokens: 12 } } }], interrupts: [] })
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
  expect(s.events.at(-1)).toMatchObject({ event: 'interaction.request', data: { kind: 'research_question', question: '分析目标？' } })
  await s.command('interaction.answer', { run_id: run, interaction_id: 'research', answer: '比较两组均值' })
  expect(s.request).toHaveBeenCalledWith('run.resume', { thread_id: snapshot.thread_id, run_id: run, decisions: { research: '比较两组均值' } })
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.interrupted', { interrupts: [{ id: 'approval', value: { action_requests: [{ name: 'execute_python', args: { code: 'print(1)' } }] } }] }) })
  await s.application.serial(async () => {})
  await expect(s.command('interaction.answer', { run_id: run, interaction_id: 'approval', answer: 'anything' })).rejects.toThrow('审批')
  await s.command('interaction.answer', { run_id: run, interaction_id: 'approval', answer: 'reject' })
  expect(s.request).toHaveBeenCalledWith('run.resume', { thread_id: snapshot.thread_id, run_id: run, decisions: { approval: { decisions: [{ type: 'reject' }] } } })
})

it('persists settings across projects, returns renderer-safe configuration and source plugin envelopes', async () => {
  const s = setup(); await s.application.initialize()
  await s.command('model.configure', { model_code: 'deepseek-flash' })
  await s.command('context.configure', { max_tokens: '32000' })
  await s.command('permission.set', { mode: 'read_only' })
  await s.command('exa.configure', { api_key: 'private-test-key' })
  expect(JSON.stringify(s.application.state())).not.toContain('private-test-key')
  expect(s.application.state().plugins.filter(p => p.id === 'exa' || p.id === 'data_analysis')).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: 'exa', configured: false, loaded: false }),
    expect.objectContaining({ id: 'data_analysis', configured: false, loaded: false })
  ]))
  await s.command('model.test')
  await s.command('project.create', { name: '新项目' })
  expect(s.application.state()).toMatchObject({ max_context_tokens: 32000, permission_mode: 'read_only' })
  expect(await s.command('plugin.snapshot', { plugin_id: 'data_analysis' })).toEqual({ plugin_id: 'data_analysis', state: {} })
  await s.command('exa.clear')
  expect(s.application.state().plugins.find(p => p.id === 'exa')?.enabled).toBe(false)
})

it('stops the exact running checkpoint thread', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '项目' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '执行任务' }, run)
  await s.command('run.stop', { run_id: run })
  expect(s.request).toHaveBeenCalledWith('run.stop', { run_id: run, thread_id: snapshot.thread_id })
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
