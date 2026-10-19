import { expect, it } from 'vitest'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { Backend } from '../src/main/backend'
import { DesktopApplication } from '../src/main/application'
import type { Command, EventFrame, UserProfile } from '../src/shared/protocol'

it.skipIf(process.env.DESKTOP_LIVE_TEST !== '1')('runs DeepSeek with real attachments, workspace tools, approval, usage and checkpoint restoration', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'desktop-live-'))
  mkdirSync(path.resolve(import.meta.dirname, '../test-artifacts'), { recursive: true })
  const user: UserProfile = { userID: 9001, realName: 'Live test', userName: 'live', userAvatar: '', userPhone: '', userCredit: 0, isCo: false, isBeta: false }
  const events: EventFrame[] = []
  let application: DesktopApplication
  let backend = new Backend(path.resolve(import.meta.dirname, '../..'), frame => application.receive(frame))
  const waiters = new Set<(frame: EventFrame) => void>()
  const receive = (frame: EventFrame) => { events.push(frame); for (const waiter of waiters) waiter(frame) }
  const waitFor = (predicate: (frame: EventFrame) => boolean, timeout = 180000): Promise<EventFrame> => new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiters.delete(check); reject(new Error(`Live event timeout: ${JSON.stringify(events.slice(-3))}`)) }, timeout)
    const check = (frame: EventFrame) => { if (predicate(frame)) { clearTimeout(timer); waiters.delete(check); resolve(frame) } }
    waiters.add(check)
  })
  const command = (method: string, params = {}, id = randomUUID()) => application.serial(() => application.command({ id, method, params } as Command))
  try {
    application = new DesktopApplication(directory, backend, () => user, receive)
    await application.initialize()
    await command('project.create', { name: '真实接口验证' })
    await command('permission.set', { mode: 'full' })
    await command('model.test')
    const source = path.join(directory, 'numbers.csv'); writeFileSync(source, 'value\n2\n4\n6\n')
    const [attachment] = application.storage().importAttachments([source])
    const run = randomUUID()
    const complete = waitFor(frame => frame.event === 'run.finished' && frame.run_id === run)
    await command('run.attachments', { text: '读取消息附带CSV内容，计算value列总和。必须调用 write_file 工具创建虚拟路径 /summary.txt，内容仅为 sum=实际总和，然后简短回复完成。不要输出代码或调用子代理。', attachment_ids: [attachment.id] }, run)
    const result = await complete
    writeFileSync(path.resolve(import.meta.dirname, '../test-artifacts/live-events.json'), JSON.stringify(events, null, 2))
    expect(result).toMatchObject({ event: 'run.finished', data: { status: 'complete' } })
    const failures = events.filter(frame => frame.event === 'protocol.error'); expect(failures).toEqual([])
    const toolNames = events.flatMap(frame => frame.event === 'run.event' && frame.data.kind === 'tool_start' ? [frame.data.name] : [])
    expect(toolNames).toContain('write_file')
    expect(events.some(frame => frame.event === 'run.event' && frame.data.kind === 'tool_start' && frame.data.name === 'write_file' && frame.data.arguments != null)).toBe(true)
    expect(events.some(frame => frame.event === 'run.event' && frame.data.kind === 'tool_end' && frame.data.name === 'write_file' && frame.data.output != null && typeof frame.data.elapsed === 'number')).toBe(true)
    const snapshot = await command('session.snapshot') as any
    expect(snapshot.messages.some((message: any) => message.type === 'ai' && message.data.content)).toBe(true)
    expect(readFileSync(path.join(application.workspace(), 'summary.txt'), 'utf8')).toMatch(/^sum=12\r?\n?$/)
    expect(snapshot.totals.total_tokens).toBeGreaterThan(0)
    expect(await command('usage.stats', { period: '', model: '' })).toMatchObject({ totals: { requests: expect.any(Number) } })
    const tokenUsage = await command('session.token_usage') as any; expect(tokenUsage.used_tokens).toBeGreaterThan(0)
    const exported = await command('session.export', { session_id: snapshot.session_id }) as any; expect(exported.text).toContain('用户')
    await backend.close()
    backend = new Backend(path.resolve(import.meta.dirname, '../..'), frame => application.receive(frame))
    application = new DesktopApplication(directory, backend, () => user, receive)
    await application.initialize()
    expect(await command('session.snapshot')).toMatchObject({ session_id: snapshot.session_id, messages: snapshot.messages, attachments: snapshot.attachments, artifacts: snapshot.artifacts })
    await command('permission.set', { mode: 'ask' })
    const approvalRun = randomUUID()
    const approval = waitFor(frame => frame.event === 'interaction.request' && frame.run_id === approvalRun)
    await command('run.start', { text: '必须调用 write_file 创建 /approved.txt 内容 approved，不调用子代理，完成后回复完成。' }, approvalRun)
    const question = await approval
    expect(question).toMatchObject({ data: { kind: 'approval' } })
    expect(existsSync(path.join(application.workspace(), 'approved.txt'))).toBe(false)
    const approved = waitFor(frame => frame.event === 'run.finished' && frame.run_id === approvalRun)
    await command('interaction.answer', { run_id: approvalRun, interaction_id: (question as any).data.interaction_id, answer: 'approve' })
    expect(await approved).toMatchObject({ data: { status: 'complete' } })
    expect(readFileSync(path.join(application.workspace(), 'approved.txt'), 'utf8')).toMatch(/^approved\r?\n?$/)
    const rejectedRun = randomUUID()
    const rejection = waitFor(frame => frame.event === 'interaction.request' && frame.run_id === rejectedRun)
    await command('run.start', { text: '调用 write_file 创建 /rejected.txt 内容 rejected；如果用户拒绝，立即回复已取消，不再尝试写文件。不调用子代理。' }, rejectedRun)
    const rejectedQuestion = await rejection
    const rejected = waitFor(frame => frame.event === 'run.finished' && frame.run_id === rejectedRun)
    await command('interaction.answer', { run_id: rejectedRun, interaction_id: (rejectedQuestion as any).data.interaction_id, answer: 'reject' })
    expect(await rejected).toMatchObject({ data: { status: 'complete' } })
    expect(existsSync(path.join(application.workspace(), 'rejected.txt'))).toBe(false)
    const pausedRun = randomUUID()
    const paused = waitFor(frame => frame.event === 'interaction.request' && frame.run_id === pausedRun)
    await command('run.start', { text: '调用 write_file 创建 /cancelled.txt 内容 cancelled。不调用子代理。' }, pausedRun)
    await paused
    const pausedCancelled = waitFor(frame => frame.event === 'run.finished' && frame.run_id === pausedRun)
    await command('run.stop', { run_id: pausedRun })
    expect(await pausedCancelled).toMatchObject({ data: { status: 'cancelled', snapshot: { unsettled_history: false } } })
    expect(existsSync(path.join(application.workspace(), 'cancelled.txt'))).toBe(false)
    const continuedRun = randomUUID()
    const continued = waitFor(frame => frame.event === 'run.finished' && frame.run_id === continuedRun)
    await command('run.start', { text: '只回复 cancel-ok，不调用任何工具。' }, continuedRun)
    expect(await continued).toMatchObject({ data: { status: 'complete' } })
    const stopping = randomUUID(), cancelled = waitFor(frame => frame.event === 'run.finished' && frame.run_id === stopping, 30000)
    await command('run.start', { text: '请详细分析数字序列的统计性质。' }, stopping)
    await command('run.stop', { run_id: stopping })
    expect(await cancelled).toMatchObject({ data: { status: 'cancelled' } })
  } finally {
    await backend.close()
    writeFileSync(path.resolve(import.meta.dirname, '../test-artifacts/live-events.json'), JSON.stringify(events, null, 2))
    rmSync(directory, { recursive: true, force: true })
  }
}, 240000)
