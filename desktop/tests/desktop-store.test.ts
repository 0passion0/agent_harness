import { afterEach, describe, expect, it } from 'vitest'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { DesktopStore } from '../src/main/desktop-store'
import { LocalStore } from '../src/main/local-store'

const roots: string[] = []
function temp(): string { const root = mkdtempSync(path.join(tmpdir(), 'desktop-store-')); roots.push(root); return root }
afterEach(() => { for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true }) })

describe('DesktopStore', () => {
  it('fills missing usage on completion without duplicating or overwriting measured requests', () => {
    const root = temp()
    const store = new DesktopStore(path.join(root, 'project'), path.join(root, 'workspace'), path.join(root, 'account'))
    const record = { request_id: 'request', requested_at: '2026-01-01T00:00:00Z', model: 'fallback', source: 'model' as const, usage: null, cost_estimate: null }
    store.recordUsage('p', 's', record)
    store.recordUsage('p', 's', { ...record, requested_at: '2026-01-02T00:00:00Z', model: 'actual', usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 } })
    store.recordUsage('p', 's', record)
    const stats = store.usageStats('', '')
    expect(stats.totals).toMatchObject({ requests: 1, reported_requests: 1, total_tokens: 15 })
    expect(stats.model_names).toEqual(['actual'])
    expect(stats.dates).toEqual(['2026-01-01'])
  })
  it('binds selected project paths and deletes only that project session', () => {
    const root = temp(); const account = new LocalStore(root); const project = account.createProject('one')
    const workspace = path.join(root, 'selected'); mkdirSync(workspace)
    expect(account.bindProject(project.id, workspace).path).toBe(workspace)
    const session = account.createSession(); expect(account.deleteSession(session.id)).toEqual([])
    expect(() => account.resume(session.id)).toThrow()
  })

  it('imports attachments into workspace and resolves only manifest references', () => {
    const root = temp(); const workspace = path.join(root, 'workspace'); mkdirSync(workspace)
    const source = path.join(root, 'source.txt'); writeFileSync(source, 'hello')
    const store = new DesktopStore(path.join(root, 'project'), workspace, path.join(root, 'account'))
    const [ref] = store.importAttachments([source])
    expect(ref).toMatchObject({ name: 'source.txt', media_type: 'text/plain', size: 5, relative_path: `attachments/${ref.id}/source.txt` })
    expect(readFileSync(store.resolveAttachment(ref.id), 'utf8')).toBe('hello')
    expect(store.readAttachment(ref.id)).toEqual({ media_type: 'text/plain', data: 'aGVsbG8=' })
    expect(() => store.resolveAttachment('../outside')).toThrow()
  })

  it('rejects attachment destinations that traverse a symlink', () => {
    const root = temp(); const workspace = path.join(root, 'workspace'); mkdirSync(workspace)
    const outside = path.join(root, 'outside'); mkdirSync(outside)
    const link = path.join(workspace, 'attachments')
    symlinkSync(outside, link, 'junction')
    const source = path.join(root, 'source.txt'); writeFileSync(source, 'secret')
    const store = new DesktopStore(path.join(root, 'project'), workspace, path.join(root, 'account'))
    expect(() => store.importAttachments([source])).toThrow()
  })

  it('rejects a symlinked config root before writing through it', () => {
    const root = temp(); const project = path.join(root, 'project'); const workspace = path.join(root, 'workspace'); const outside = path.join(root, 'outside')
    mkdirSync(project); mkdirSync(workspace); mkdirSync(outside)
    symlinkSync(outside, path.join(project, 'config'), 'junction')
    const store = new DesktopStore(project, workspace, path.join(root, 'account'))
    expect(() => store.saveConfig('project', 'agent', 'must not escape')).toThrow()
    expect(() => readFileSync(path.join(outside, 'agent.md'), 'utf8')).toThrow()
  })

  it('requires published workspace artifact paths and retains their source run reference', () => {
    const root = temp(); const workspace = path.join(root, 'workspace'); mkdirSync(path.join(workspace, 'reports'), { recursive: true })
    writeFileSync(path.join(workspace, 'reports', 'result.csv'), 'x,y\n1,2\n')
    const store = new DesktopStore(path.join(root, 'project-id'), workspace, path.join(root, 'account'))
    const ref = store.publishArtifact('/workspace/reports/result.csv', 'run-7')
    expect(ref).toMatchObject({ workspace_id: 'project-id', name: 'result.csv', relative_path: '/workspace/reports/result.csv', source_run_id: 'run-7' })
    expect(store.listArtifacts()).toEqual([ref])
    expect(store.readArtifact(ref.relative_path).media_type).toBe('text/csv')
    expect(() => store.publishArtifact('../outside.txt', 'run-8')).toThrow()
    expect(() => store.resolveArtifact('/workspace/reports/unpublished.csv')).toThrow()
  })

  it('removes project config without changing global files and preserves full usage summary shape', () => {
    const root = temp(); const workspace = path.join(root, 'workspace'); mkdirSync(workspace)
    const account = path.join(root, 'account')
    const store = new DesktopStore(path.join(root, 'project'), workspace, account)
    store.saveConfig('global', 'agent', 'global')
    store.saveConfig('project', 'agent', 'project')
    expect(store.removeConfig('agent').agent.content).toBe('global')
    store.recordUsage('p', 's', { request_id: '1', requested_at: '2026-01-01T01:00:00+08:00', model: 'm', source: 'model', usage: { input_tokens: 1, output_tokens: 2, total_tokens: 3, input_token_details: { cache_read: 1 }, output_token_details: { reasoning: 1 } }, cost_estimate: { minimum_cny: '0.1', maximum_cny: '0.2', minimum_credits: '0.3', maximum_credits: '0.4', rate_dates: ['2026-01-01'] } })
    store.recordUsage('p', 's', { request_id: '2', requested_at: '2026-01-01T01:30:00+08:00', model: 'm', source: 'model', usage: { input_tokens: 2, output_tokens: 3, total_tokens: 5, input_token_details: { cache_read: 2 }, output_token_details: { reasoning: 2 } }, cost_estimate: { minimum_cny: '0.2', maximum_cny: '0.4', minimum_credits: '0.5', maximum_credits: '0.6', rate_dates: ['2026-01-02'] } })
    store.recordUsage('p', 's', { request_id: '3', requested_at: '2026-01-01T02:00:00+08:00', model: 'm', source: 'jev', usage: null, cost_estimate: null })
    const stats = store.usageStats('2026-01', '')
    expect(stats).toMatchObject({ timezone: 'Asia/Shanghai', tracking_since: '2026-01-01', dates: ['2026-01-01'], model_names: ['m'], totals: { requests: 3, reported_requests: 2, jev_requests: 1, unpriced_requests: 0, input_tokens: 3, output_tokens: 5, total_tokens: 8, cache_read: null, reasoning: null, cost_estimate: { minimum_cny: '0.3', maximum_cny: '0.6', minimum_credits: '0.8', maximum_credits: '1', rate_dates: ['2026-01-01', '2026-01-02'] } } })
    expect(store.usageStats('2026-01', 'm').models).toHaveLength(2)
    store.recordUsage('other-project', 's', { request_id: '4', requested_at: '2026-01-01T02:00:00+08:00', model: 'm', source: 'model', usage: { input_tokens: 100, output_tokens: 0, total_tokens: 100 }, cost_estimate: null })
    store.recordUsage('p', 'other-session', { request_id: '5', requested_at: '2026-01-01T02:00:00+08:00', model: 'm', source: 'model', usage: { input_tokens: 200, output_tokens: 0, total_tokens: 200 }, cost_estimate: null })
    expect(store.usageStats('', '', { projectId: 'p', sessionId: 's' }).totals).toMatchObject({ requests: 3, total_tokens: 8 })
  })
})
