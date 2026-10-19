import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'
import { existsSync } from 'node:fs'
import path from 'node:path'
import type { Json } from '../shared/protocol'

export type Interrupt = { id: string; value: { action_requests?: { name: string; args: Json }[]; [key: string]: Json | undefined } }
export type EngineEvent = { type: string; run_id: string; thread_id: string; operation_id: string; name: string; agent_name: string | null; data: Record<string, Json> }
export type EngineFrame =
  | { event: 'agent.event'; data: EngineEvent }
  | { event: 'run.cancelled'; data: { run_id: string; thread_id: string } }
  | { event: 'backend.exited'; data: { message: string } }

export class Backend {
  private child: ChildProcessWithoutNullStreams | null = null
  private nextId = 0
  private pending = new Map<number, { resolve: (result: any) => void; reject: (error: Error) => void }>()
  private closing = false
  constructor(private root: string, private emit: (event: EngineFrame) => void) {}

  private start(): void {
    const python = path.join(this.root, '.venv', 'Scripts', 'python.exe')
    if (!existsSync(python)) throw new Error('未找到项目 .venv，请先按项目说明安装 Python 依赖')
    const child = spawn(python, ['-X', 'utf8', '-u', path.join(this.root, 'desktop', 'bridge.py')], { cwd: this.root, windowsHide: true, stdio: 'pipe' })
    this.child = child
    createInterface({ input: child.stdout }).on('line', line => {
      const frame = JSON.parse(line)
      if ('id' in frame) {
        const request = this.pending.get(frame.id)
        if (!request) throw new Error('未知的后端响应 ID')
        this.pending.delete(frame.id)
        if (frame.error) request.reject(new Error(frame.error.message))
        else request.resolve(frame.result)
      } else if (frame.method === 'agent.event') this.emit({ event: 'agent.event', data: frame.params })
      else if (frame.method === 'run.cancelled') this.emit({ event: 'run.cancelled', data: frame.params })
      else throw new Error('未知的后端通知')
    })
    child.stderr.on('data', data => process.stderr.write(data))
    child.once('error', error => this.failed(error))
    child.once('exit', (code, signal) => this.failed(new Error(`Python 进程已退出 (${code ?? signal})`)))
  }

  private failed(error: Error): void {
    this.child = null
    for (const request of this.pending.values()) request.reject(error)
    this.pending.clear()
    if (!this.closing) this.emit({ event: 'backend.exited', data: { message: error.message } })
  }

  request<T>(method: string, params: object): Promise<T> {
    if (!this.child) this.start()
    const id = ++this.nextId
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.child!.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n', error => {
        if (error) { this.pending.delete(id); reject(error) }
      })
    })
  }

  async close(): Promise<void> {
    this.closing = true
    if (this.child) {
      const child = this.child
      const exited = new Promise<void>(resolve => child.once('exit', () => resolve()))
      await this.request('shutdown', {})
      await exited
    }
  }
}
