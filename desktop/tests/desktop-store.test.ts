import { afterEach, describe, expect, it } from 'vitest'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { inflateRawSync } from 'node:zlib'
import path from 'node:path'
import { DesktopStore } from '../src/main/desktop-store'
import { LocalStore } from '../src/main/local-store'

const roots: string[] = []
function temp(): string { const root = mkdtempSync(path.join(tmpdir(), 'desktop-store-')); roots.push(root); return root }
afterEach(() => { for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true }) })

describe('DesktopStore', () => {
  it('binds selected project paths and deletes only that project session', () => {
    const root = temp(); const account = new LocalStore(root, 12); const project = account.createProject('one')
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

  it('persists settings, config, skills and usage with scoped paths', () => {
    const root = temp(); const workspace = path.join(root, 'workspace'); mkdirSync(workspace)
    const store = new DesktopStore(path.join(root, 'project'), workspace, path.join(root, 'account'))
    store.saveSettings({ model_code: 'model-x', exa_api_key: 'secret' })
    expect(store.readSettings().model_code).toBe('model-x')
    expect(store.readSettingsView()).not.toHaveProperty('exa_api_key')
    store.saveConfig('project', 'agent', 'Follow the project instructions.')
    store.saveConfig('project', 'mcp', JSON.stringify({ mcpServers: { example: { transport: 'stdio', command: 'node' } } }))
    expect(() => store.saveConfig('project', 'mcp', '{"mcpServers":{"same":{"command":"a"},"same":{"command":"b"}}}')).toThrow(/duplicate key: same/)
    const skillSource = path.join(root, 'example'); mkdirSync(skillSource)
    writeFileSync(path.join(skillSource, 'SKILL.md'), '---\nname: example\n---\nSkill body')
    store.importSkill('project', skillSource)
    store.setSkill('project', 'example', false)
    expect(store.inspectConfig('project')).toMatchObject({ agent: { content: 'Follow the project instructions.' }, mcp: { servers: { example: { transport: 'stdio', command: 'node' } } }, skills: [{ id: 'example', enabled: false }] })
    expect(store.readSkill('project', 'example').content).toContain('Skill body')
    store.recordUsage('project-id', 'session-id', { request_id: 'request-1', requested_at: '2026-01-01T00:00:00Z', model: 'model-x', source: 'model', usage: { input_tokens: 2, output_tokens: 3, total_tokens: 5 }, cost_estimate: null })
    expect(store.usageStats('', '').totals).toMatchObject({ requests: 1, input_tokens: 2, output_tokens: 3, total_tokens: 5 })
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

  it('edits the installed data_analysis resource as an account draft and exports a real ZIP', () => {
    const root = temp(); const workspace = path.join(root, 'workspace'); const source = path.join(root, 'plugin-source')
    mkdirSync(workspace); mkdirSync(path.join(source, 'prompts'), { recursive: true }); mkdirSync(path.join(source, 'guides'))
    writeFileSync(path.join(source, 'prompts', 'main.md'), 'original')
    writeFileSync(path.join(source, 'guides', 'guide.md'), 'guide')
    writeFileSync(path.join(source, 'module.py'), 'value = 1\n')
    const account = path.join(root, 'account')
    const store = new DesktopStore(path.join(root, 'project'), workspace, account, source)
    expect(store.pluginFiles('data_analysis')).toEqual({ files: ['guides/guide.md', 'module.py', 'prompts/main.md'] })
    expect(store.pluginDirectory('data_analysis')).toBe(source)
    expect(() => store.savePlugin('data_analysis', 'module.py', 'def broken(:\n')).toThrow(/SyntaxError/)
    store.savePlugin('data_analysis', 'prompts/main.md', 'edited')
    expect(store.pluginDirectory('data_analysis')).toBe(path.join(account, 'plugins', 'data_analysis'))
    expect(store.readPlugin('data_analysis', 'prompts/main.md')).toEqual({ content: 'edited' })
    expect(readFileSync(path.join(source, 'prompts', 'main.md'), 'utf8')).toBe('original')
    expect(readFileSync(path.join(account, 'plugins/data_analysis/guides/guide.md'), 'utf8')).toBe('guide')
    const exported = store.exportPlugin('data_analysis')
    const zip = readFileSync(exported.path)
    expect(zip.readUInt32LE(0)).toBe(0x04034b50)
    expect(zip.includes(Buffer.from('prompts/main.md'))).toBe(true)
    const payloads: Buffer[] = []
    let offset = 0
    while (zip.readUInt32LE(offset) === 0x04034b50) {
      const nameLength = zip.readUInt16LE(offset + 26); const extraLength = zip.readUInt16LE(offset + 28); const compressedLength = zip.readUInt32LE(offset + 18)
      const start = offset + 30 + nameLength + extraLength
      payloads.push(inflateRawSync(zip.subarray(start, start + compressedLength)))
      offset = start + compressedLength
    }
    expect(Buffer.concat(payloads).toString()).toContain('edited')
    expect(() => store.pluginFiles('user_config')).toThrow()
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
  })
})
