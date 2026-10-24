import { afterEach, expect, it, vi } from 'vitest'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
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
    if (method === 'catalog.get' || method.startsWith('harness.')) return { }
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

it('runs independent sessions, restores live output on navigation and stops only the selected run', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '并发项目' })
  const first = await s.command('session.snapshot') as any, one = randomUUID(), two = randomUUID()
  await s.command('run.start', { text: '第一任务' }, one)
  await expect(s.command('run.start', { text: '重复任务' })).rejects.toThrow('当前会话')
  const second = await s.command('session.clear') as any
  await s.command('run.start', { text: '第二任务' }, two)
  s.application.receive({ event: 'agent.event', data: event(one, first.thread_id, 'model.delta', { text: '第一会话输出' }) })
  s.application.receive({ event: 'agent.event', data: event(two, second.thread_id, 'model.delta', { text: '第二会话输出' }) })
  await s.application.serial(async () => {})
  expect(await s.command('session.list')).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: first.thread_id, status: 'running' }),
    expect.objectContaining({ id: second.thread_id, status: 'running' })
  ]))
  expect(await s.command('session.resume', { session_id: first.thread_id })).toMatchObject({
    active_run: { id: one, phase: 'running', records: [expect.objectContaining({ text: '第一会话输出' })] }
  })
  await s.command('run.stop', { run_id: one })
  expect(s.request).toHaveBeenLastCalledWith('run.stop', { run_id: one, thread_id: first.thread_id })
  s.application.receive({ event: 'run.cancelled', data: { run_id: one, thread_id: first.thread_id } })
  await s.application.serial(async () => {})
  expect(await s.command('session.resume', { session_id: second.thread_id })).toMatchObject({ active_run: { id: two, phase: 'running' } })
  await s.command('context.configure', { max_tokens: '64000' })
  await expect(s.command('model.test')).resolves.toBeDefined()
  await expect(s.command('session.export', { session_id: second.thread_id })).resolves.toBeDefined()
})

it('archives background completions in the original project without changing the selected project', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '原项目' })
  const original = s.application.state().active_project_id!, first = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '后台任务' }, run)
  await s.command('project.create', { name: '当前项目' })
  const selected = s.application.state().active_project_id!
  const current = await s.command('session.snapshot') as any
  await s.command('run.start', { text: '前台任务' })
  s.application.receive({ event: 'agent.event', data: event(run, first.thread_id, 'model.completed', { message_id: 'background-model', usage: { input_tokens: 7, output_tokens: 3, total_tokens: 10 } }) })
  s.application.receive({ event: 'agent.event', data: event(run, first.thread_id, 'run.completed') })
  await s.application.serial(async () => {})
  expect(s.application.state().active_project_id).toBe(selected)
  expect(await s.command('session.snapshot')).toMatchObject({ session_id: current.thread_id, active_run: { phase: 'running' } })
  const archived = JSON.parse(readFileSync(path.join(s.directory, 'projects', original, 'sessions', `${first.thread_id}.json`), 'utf8'))
  expect(archived.runs).toHaveLength(1)
  expect(archived.runs[0]).toMatchObject({ id: run, status: 'complete' })
  expect(s.events.find(frame => frame.event === 'run.finished' && frame.run_id === run)).toMatchObject({ session_id: first.thread_id })
  await s.command('project.select', { project_id: original })
  const usage = await s.command('usage.stats', { period: '', model: '' }) as any
  expect(usage.totals.total_tokens).toBe(10)
})

it('deletes one running session while retaining another live session', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '删除隔离' })
  const first = await s.command('session.snapshot') as any, one = randomUUID()
  await s.command('run.start', { text: '删除此任务' }, one)
  const second = await s.command('session.clear') as any, two = randomUUID()
  await s.command('run.start', { text: '保留此任务' }, two)
  await s.command('session.delete', { session_id: first.thread_id })
  s.application.receive({ event: 'agent.event', data: event(one, first.thread_id, 'run.completed') })
  await s.application.serial(async () => {})
  expect(existsSync(path.join(s.directory, 'projects', s.application.state().active_project_id!, 'sessions', `${first.thread_id}.json`))).toBe(false)
  expect(await s.command('session.snapshot')).toMatchObject({ session_id: second.thread_id, active_run: { id: two } })
})

it('manages resources by explicit project without switching chats and permits edits during runs', async () => {
  const s = setup(); await s.application.initialize()
  await expect(s.command('harness.inspect', { scope: 'project', project_id: 'unknown' })).rejects.toThrow('项目')
  s.request.mockResolvedValueOnce({} as any)
  await s.command('harness.inspect', { scope: 'global', project_id: '' })
  expect(s.request).toHaveBeenLastCalledWith('harness.inspect', { scope: 'global', project_id: '', workspace_root: null })
  await s.command('project.create', { name: '技能项目' })
  const firstPath = path.join(s.directory, 'real-project-folder'); mkdirSync(firstPath)
  await s.application.bindWorkspace(firstPath)
  const firstProject = s.application.state().active_project_id!
  await s.command('project.create', { name: '另一个项目' })
  expect(await s.command('harness.projects')).toContainEqual({ id: firstProject, name: 'real-project-folder', path: firstPath })
  s.request.mockResolvedValueOnce({} as any)
  await s.command('harness.save', { scope: 'project', project_id: firstProject, kind: 'agent', name: '', content: 'constraints' })
  expect(s.request).toHaveBeenLastCalledWith('harness.save', { scope: 'project', project_id: firstProject, kind: 'agent', name: '', content: 'constraints', workspace_root: firstPath })
  expect(s.application.state().active_project_id).not.toBe(firstProject)
  s.request.mockResolvedValueOnce({} as any)
  await s.application.importSkills('global', '', 'C:/skill.zip')
  expect(s.request).toHaveBeenLastCalledWith('harness.import', { scope: 'global', archive_path: 'C:/skill.zip', workspace_root: null })
  await s.command('run.start', { text: 'run' })
  await expect(s.command('harness.remove', { scope: 'global', project_id: '', kind: 'skill', name: 'example' })).resolves.toEqual({})
  await expect(s.application.importSkills('global', '', 'C:/skill.zip')).resolves.toEqual({})
})

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
  const originalProject = s.application.state().active_project_id!
  await s.command('project.create', { name: '运行中' })
  await s.command('project.select', { project_id: originalProject })
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
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'run.interrupted', { interrupts: [{ id: 'approval', value: { type: 'tool_approval', tool_call_id: 'python', name: 'execute_python_file', args: { file_path: '/analysis.py' }, description: 'Execute script' } }] }) })
  await s.application.serial(async () => {})
  expect(s.events.at(-1)).toMatchObject({ event: 'interaction.request', data: { kind: 'approval', action_requests: [{ name: 'execute_python_file', args: { file_path: '/analysis.py' } }] } })
  expect((s.events.at(-1) as Extract<EventFrame, { event: 'interaction.request' }>).data.question).not.toContain('print(1)')
  await expect(s.command('interaction.answer', { run_id: run, interaction_id: 'approval', answer: 'anything' })).rejects.toThrow('审批')
  await s.command('interaction.answer', { run_id: run, interaction_id: 'approval', answer: 'reject' })
  expect(s.request).toHaveBeenCalledWith('run.resume', { thread_id: snapshot.thread_id, run_id: run, decisions: { approval: { type: 'reject' } } })
})

it.each(['0', '475001', '1.5'])('rejects an invalid context budget %s before saving', async max => {
  const s = setup(); await s.application.initialize()
  expect(s.application.state().max_context_tokens).toBe(475000)
  await expect(s.command('context.configure', { max_tokens: max })).rejects.toThrow('475,000')
  expect(s.application.state().max_context_tokens).toBe(475000)
})

it('stops the exact running checkpoint thread', async () => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '项目' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '执行任务' }, run)
  await s.command('run.stop', { run_id: run })
  expect(s.request).toHaveBeenCalledWith('run.stop', { run_id: run, thread_id: snapshot.thread_id })
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

it.each([true, false])('restores a durable paused run after backend exit when interrupt notification was delivered: %s', async delivered => {
  const s = setup(); await s.application.initialize(); await s.command('project.create', { name: '恢复' })
  const snapshot = await s.command('session.snapshot') as any, run = randomUUID()
  await s.command('run.start', { text: '继续' }, run)
  s.application.receive({ event: 'agent.event', data: event(run, snapshot.thread_id, 'custom.progress', { kind: 'workflow.node', node: 'review', status: 'running' }) })
  const interrupts: Interrupt[] = [{ id: 'question-1', value: { kind: 'question', question: '补充说明？', options: [] } }]
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
