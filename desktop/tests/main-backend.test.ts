import { expect, it, vi } from 'vitest'
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { Backend } from '../src/main/backend'
import type { EngineFrame } from '../src/main/backend'

vi.mock('node:child_process', { spy: true })

it('starts the repository Python bridge, configures isolated paths and shuts down', async () => {
  const temporary = mkdtempSync(path.join(tmpdir(), 'harness-rpc-'))
  const previous = process.env.VA_MODEL
  process.env.VA_MODEL = 'test:中文模型'
  const events: EngineFrame[] = []
  const backend = new Backend(path.resolve(import.meta.dirname, '../..'), event => events.push(event))
  try {
    expect(await backend.request('configure', {
      tenant_id: 'project', user_id: '7', workspace_root: path.join(temporary, 'workspace'), runtime_data_dir: temporary,
      options: { model_code: 'test:中文模型', enabled_plugins: [], runtimes: { python: '', node: '' }, permission_mode: 'ask', max_context_tokens: 128000, agent_content: '', skills: [], mcp_servers: {} }
    })).toMatchObject({ model: 'test:中文模型' })
  } finally {
    await backend.close()
    if (previous === undefined) delete process.env.VA_MODEL
    else process.env.VA_MODEL = previous
    expect(path.dirname(path.resolve(temporary))).toBe(path.resolve(tmpdir()))
    rmSync(temporary, { recursive: true })
  }
  expect(events).toEqual([])
}, 20000)

it('reports an unexpected Python process exit before another request can restart it', async () => {
  let notifyExit!: (frame: EngineFrame) => void
  const exited = new Promise<EngineFrame>(resolve => { notifyExit = resolve })
  const backend = new Backend(path.resolve(import.meta.dirname, '../..'), frame => notifyExit(frame))
  try {
    await backend.request('environment', {})
    const child = vi.mocked(spawn).mock.results.at(-1)!.value
    expect(child.kill()).toBe(true)
    expect(await exited).toMatchObject({ event: 'backend.exited', data: { message: expect.stringContaining('Python 进程已退出') } })
  } finally {
    await backend.close()
  }
}, 20000)
