import { afterEach, describe, expect, it, vi } from 'vitest'
import { createHmac } from 'node:crypto'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
vi.mock('electron', () => ({ safeStorage: {
  isEncryptionAvailable: () => true,
  encryptString: (text: string) => Buffer.from(text),
  decryptString: (buffer: Buffer) => buffer.toString()
} }))
import { AuthStore, ticketProfile } from '../src/main/auth-store'
import { LocalStore } from '../src/main/local-store'
import { assertSender, validateCommand } from '../src/main/commands'
import { isSsoCallback } from '../src/main/sso-validation'
import { medpeerUrls } from '../src/shared/medpeer-urls'

const directories: string[] = []
function directory() { const value = mkdtempSync(path.join(tmpdir(), 'harness-desktop-')); directories.push(value); return value }
afterEach(() => { vi.unstubAllGlobals(); for (const value of directories.splice(0)) rmSync(value, { recursive: true }) })
const details = { user_id: 7, user_phone: '123', user_credit: 10 }
function ticket(overrides = {}) {
  const header = Buffer.from('{}').toString('base64')
  const payload = { userID: 7, exp: Date.now() / 1000 + 3600, realName: '测试', userName: 'test', userAvatar: '', token: 'secret', isCo: false, isBeta: false, ...overrides }
  const body = Buffer.from(JSON.stringify(payload)).toString('base64')
  const signature = Buffer.from(createHmac('sha256', `MedPeer_${payload.userID}`).update(header + body).digest('hex')).toString('base64')
  return `${header}.${body}.${signature}`
}

describe('desktop trust boundaries', () => {
  it('requires the exact renderer document URL for IPC', () => {
    const trusted = 'http://localhost:5173/'
    expect(() => assertSender(trusted, trusted)).not.toThrow()
    for (const actual of ['http://localhost:5174/', 'http://other:5173/', 'http://localhost:5173/other', 'http://localhost:5173/?other', 'http://localhost:5173/#other', 'file:///C:/other.html']) {
      expect(() => assertSender(actual, trusted)).toThrow('Untrusted IPC sender')
    }
  })
  it('accepts only declared IPC methods and exact fields', () => {
    expect(validateCommand({ id: '1', method: 'run.start', params: { text: 'hi' } }).method).toBe('run.start')
    expect(validateCommand({ id: '1', method: 'run.start', params: { text: 'hi', selected_plugins: ['research.citations'], selected_skills: [] } }).method).toBe('run.start')
    for (const raw of [
      { id: '1', method: 'configure', params: {} },
      { id: '1', method: 'run.start', params: { text: 'hi', workspace: 'C:/' } },
      { id: '1', method: 'run.start', params: { text: '  ' } },
      { id: '1', method: 'run.start', params: { text: 'hi', selected_plugins: 'research.citations' } },
      { id: '1', method: 'run.start', params: { text: 'hi', selected_skills: ['one', 'one'] } },
      { id: '1', method: 'plugin.save', params: { plugin_id: 'research.citations', path: 'code.py', content: 'pass' } }
    ]) expect(() => validateCommand(raw)).toThrow()
    expect(validateCommand({ id: '1', method: 'interaction.answer', params: { run_id: '1', interaction_id: '2', answer: '研究问题的自由文本答复' } }).method).toBe('interaction.answer')
  })
  it('requires the precise SSO callback and authenticated matching account', () => {
    expect(isSsoCallback(medpeerUrls.callback + 'pt=x')).toBe(true)
    const wrong = new URL(medpeerUrls.callback); wrong.searchParams.set('id', 'other')
    expect(isSsoCallback(wrong.href)).toBe(false)
    expect(ticketProfile(ticket(), details).userID).toBe(7)
    expect(() => ticketProfile(ticket({ userID: 8 }), details)).toThrow('不一致')
    expect(() => ticketProfile(ticket({ exp: 0 }), details)).toThrow('过期')
    expect(() => ticketProfile(ticket() + 'x', details)).toThrow('校验失败')
  })
  it('restores sessions and isolates both accounts and projects', () => {
    const root = directory(); const first = new LocalStore(root, 7)
    const project = first.createProject('项目一'); const session = first.createSession()
    first.rename(session.id, '历史会话')
    const restored = new LocalStore(root, 7)
    expect(restored.currentSession().id).toBe(session.id)
    expect(restored.sessions()[0].title).toBe('历史会话')
    restored.createProject('项目二')
    expect(restored.sessions()).toEqual([])
    expect(() => restored.resume(session.id)).toThrow()
    restored.selectProject(project.id)
    expect(restored.currentSession().id).toBe(session.id)
    const other = new LocalStore(root, 8)
    expect(other.projects).toEqual([])
    expect(() => other.selectProject(project.id)).toThrow()
  })
  it('validates saved cookies with SSO and retains credentials on network failure', async () => {
    const auth = new AuthStore(path.join(directory(), 'credentials.bin'))
    auth.save(ticketProfile(ticket(), details), 'session=secret')
    const file = path.join(directories[0], 'credentials.bin')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ result: true, code: 200, data: details }) }))
    const restored = new AuthStore(file); await restored.restore(); expect(restored.user?.userID).toBe(7)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    await expect(new AuthStore(file).restore()).rejects.toThrow('offline')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    await restored.restore(); expect(restored.user).toBeNull()
    const cleared = new AuthStore(file); await cleared.restore(); expect(cleared.user).toBeNull()
  })
})
