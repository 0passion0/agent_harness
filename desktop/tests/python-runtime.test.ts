import { expect, it, vi } from 'vitest'
import { bundledPython, selectScriptPython } from '../src/main/python-runtime'

vi.mock('node:fs', () => ({ existsSync: vi.fn() }))

it('prefers bundled Python over a configured interpreter and falls back when absent', async () => {
  const { existsSync } = await import('node:fs')
  vi.mocked(existsSync).mockReturnValue(true)
  expect(selectScriptPython('C:/custom/python.exe', 'C:/app/resources')).toBe(bundledPython('C:/app/resources'))
  vi.mocked(existsSync).mockReturnValue(false)
  expect(selectScriptPython('C:/custom/python.exe', 'C:/app/resources')).toBe('C:/custom/python.exe')
  expect(selectScriptPython('', 'C:/app/resources', 'C:/repo/.venv/Scripts/python.exe')).toBe('C:/repo/.venv/Scripts/python.exe')
})
