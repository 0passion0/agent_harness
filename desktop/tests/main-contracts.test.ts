import { afterEach, describe, expect, it, vi } from 'vitest'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { LocalStore } from '../src/main/local-store'
import { assertSender, validateCommand } from '../src/main/commands'

const directories: string[] = []
function directory() { const value = mkdtempSync(path.join(tmpdir(), 'harness-desktop-')); directories.push(value); return value }
afterEach(() => { vi.unstubAllGlobals(); for (const value of directories.splice(0)) rmSync(value, { recursive: true }) })

describe('desktop trust boundaries', () => {
  it('validates scoped harness resource commands and keeps archive paths out of renderer commands', () => {
    const save = { id: '1', method: 'harness.save', params: { scope: 'global', project_id: '', kind: 'skill', name: 'example', content: 'body' } }
    expect(validateCommand(save)).toEqual(save)
    expect(validateCommand({ id: '2', method: 'harness.remove', params: { scope: 'project', project_id: 'selected-project', kind: 'agent', name: '' } }).method).toBe('harness.remove')
    for (const raw of [
      { ...save, params: { ...save.params, scope: 'other' } },
      { ...save, params: { ...save.params, kind: 'other' } },
      { ...save, params: { ...save.params, workspace_root: 'C:/' } },
      { id: '3', method: 'harness.import', params: { scope: 'global', archive_path: 'C:/arbitrary.zip' } }
    ]) expect(() => validateCommand(raw)).toThrow()
  })
  it('requires the exact renderer document URL for IPC', () => {
    const trusted = 'http://localhost:5173/'
    expect(() => assertSender(trusted, trusted)).not.toThrow()
    for (const actual of ['http://localhost:5174/', 'http://other:5173/', 'http://localhost:5173/other', 'http://localhost:5173/?other', 'http://localhost:5173/#other', 'file:///C:/other.html']) {
      expect(() => assertSender(actual, trusted)).toThrow('Untrusted IPC sender')
    }
  })
  it('accepts only declared IPC methods and exact fields', () => {
    expect(validateCommand({ id: '1', method: 'run.start', params: { text: 'hi' } }).method).toBe('run.start')
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
  it('restores sessions and isolates projects on local disk', () => {
    const root = directory(); const first = new LocalStore(root)
    const project = first.createProject('项目一'); const session = first.createSession()
    first.rename(session.id, '历史会话')
    const restored = new LocalStore(root)
    expect(restored.currentSession().id).toBe(session.id)
    expect(restored.sessions()[0].title).toBe('历史会话')
    restored.createProject('项目二')
    expect(restored.sessions()).toEqual([])
    expect(() => restored.resume(session.id)).toThrow()
    restored.selectProject(project.id)
    expect(restored.currentSession().id).toBe(session.id)
  })
})
