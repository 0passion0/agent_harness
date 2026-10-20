import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { expect, it, vi } from 'vitest'
import { Backend } from '../src/main/backend'
import { bundledPython } from '../src/main/python-runtime'

vi.mock('node:fs', () => ({ existsSync: vi.fn() }))
vi.mock('node:child_process', () => ({ spawn: vi.fn() }))

it.each([false, true])('selects the backend runtime with packaged=%s', packaged => {
  vi.mocked(existsSync).mockReturnValue(true)
  vi.mocked(spawn).mockImplementation(() => { throw new Error('stop before spawning') })
  const root = path.resolve('runtime-selection')
  const backend = new Backend(root, () => {}, packaged)

  expect(() => backend.request('environment', {})).toThrow('stop before spawning')

  expect(spawn).toHaveBeenLastCalledWith(
    packaged
      ? bundledPython(process.resourcesPath ?? path.join(root, 'desktop', 'resources'))
      : path.join(root, '.venv', 'Scripts', 'python.exe'),
    ['-X', 'utf8', '-u', '-m', 'vertical_harness.desktop'],
    expect.objectContaining({ cwd: root, windowsHide: true })
  )
})
