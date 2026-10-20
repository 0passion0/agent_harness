/**
 * @file backend.ts
 * @description Python 后端子进程桥接与 JSON-RPC 2.0 双向通信模块。
 * 负责拉起项目本地虚拟环境中的 Python 智能体服务引擎、管理请求/响应队列、
 * 监听标准输入输出流以及向前端分发实时引擎事件帧。
 * @author tongliga
 * @date 2026-10-16
 */

import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'
import { existsSync } from 'node:fs'
import path from 'node:path'
import type { Json } from '../shared/protocol'
import { bundledPython, selectScriptPython } from './python-runtime'

/**
 * 后端智能体执行中断（等待用户确认或输入）描述对象。
 */
export type Interrupt = {
  /** 中断交互唯一标识符 */
  id: string
  /** 中断附带的上下文数据，包含可选的工具调用申请 action_requests 等 */
  value: {
    action_requests?: { name: string; args: Json }[]
    [key: string]: Json | undefined
  }
}

/**
 * Python 智能体引擎底层推送的原始事件对象。
 */
export type EngineEvent = {
  /** 事件类型（如 model.delta、tool.started、run.interrupted 等） */
  type: string
  /** 所属运行轮次 ID */
  run_id: string
  /** 所属对话线程/会话 ID */
  thread_id: string
  /** 操作或工具调用唯一标识符 */
  operation_id: string
  /** 事件名称或步骤标题 */
  name: string
  /** 执行当前步骤的智能体或助手名称 */
  agent_name: string | null
  /** 事件序号 */
  seq?: number
  /** 上级操作或工作流节点 ID 列表 */
  parent_ids?: string[]
  /** 工作流节点标识 */
  node?: string | null
  /** 事件携带的详细载荷数据字典 */
  data: Record<string, Json>
}

/**
 * 引擎事件帧联合类型，包含事件广播、任务取消与进程异常退出通知。
 */
export type EngineFrame =
  | { event: 'agent.event'; data: EngineEvent }
  | { event: 'run.cancelled'; data: { run_id: string; thread_id: string } }
  | { event: 'backend.exited'; data: { message: string } }

/**
 * Python 智能体引擎子进程生命周期与 IPC 桥接管理器。
 */
export class Backend {
  /** 当前运行的 Python 子进程句柄 */
  private child: ChildProcessWithoutNullStreams | null = null
  /** 自增 JSON-RPC 请求 ID 计数器 */
  private nextId = 0
  /** 处于挂起等待状态的异步请求映射表 */
  private pending = new Map<number, { resolve: (result: any) => void; reject: (error: Error) => void }>()
  /** 进程是否正在主动关闭标志 */
  private closing = false

  /**
   * 初始化后端连接桥。
   *
   * @param root - 项目工作区根目录绝对物理地址
   * @param emit - 向主应用派发引擎事件帧的回调函数
   */
  constructor(
    private root: string,
    private emit: (event: EngineFrame) => void,
    private packaged = false
  ) {}

  /**
   * 启动桌面 Python 智能体引擎子进程，发布包优先使用随包运行时。
   *
   * 第一步：定位项目根目录 `.venv` 中的 python.exe 解释器。
   * 第二步：使用 Node.js spawn 派生子进程，并开启 UTF-8 与行缓冲模式。
   * 第三步：按行监听 stdout 输出流，反序列化并派发 JSON-RPC 响应或事件通知。
   * 第四步：监听 stderr、error 与 exit 事件以实现故障兜底。
   *
   * @throws Error 当未找到虚拟环境 Python 解释器时抛出
   */
  private start(): void {
    // 第一步：定位 Python 解释器绝对路径
    const resourcesPath = process.resourcesPath ?? path.join(this.root, 'desktop', 'resources')
    const bundled = bundledPython(resourcesPath)
    const python = this.packaged ? bundled : path.join(this.root, '.venv', 'Scripts', 'python.exe')
    if (!existsSync(python)) throw new Error('未找到随包 Python 或项目 .venv，请按项目说明准备 Python 运行时')

    // 第二步：派生 Python 进程
    const child = spawn(python, ['-X', 'utf8', '-u', '-m', 'vertical_harness.desktop'], {
      cwd: this.root,
      windowsHide: true,
      stdio: 'pipe'
    })
    this.child = child

    // 第三步：按行读取标准输出流并解析 JSON-RPC
    createInterface({ input: child.stdout }).on('line', line => {
      const frame = JSON.parse(line)
      if ('id' in frame) {
        const request = this.pending.get(frame.id)
        if (!request) throw new Error('未知的后端响应 ID')
        this.pending.delete(frame.id)
        if (frame.error) request.reject(new Error(frame.error.message))
        else request.resolve(frame.result)
      } else if (frame.method === 'agent.event') {
        this.emit({ event: 'agent.event', data: frame.params })
      } else if (frame.method === 'run.cancelled') {
        this.emit({ event: 'run.cancelled', data: frame.params })
      } else {
        throw new Error('未知的后端通知')
      }
    })

    // 第四步：错误与生命周期异常监听
    child.stderr.on('data', data => process.stderr.write(data))
    child.once('error', error => this.failed(error))
    child.once('exit', (code, signal) => this.failed(new Error(`Python 进程已退出 (${code ?? signal})`)))
  }

  /**
   * 处理子进程崩溃或异常退出，拒绝所有挂起请求并发出断开通知。
   *
   * @param error - 异常错误对象
   */
  private failed(error: Error): void {
    this.child = null
    for (const request of this.pending.values()) request.reject(error)
    this.pending.clear()
    if (!this.closing) this.emit({ event: 'backend.exited', data: { message: error.message } })
  }

  /**
   * 向 Python 后端发送标准 JSON-RPC 2.0 请求并等待响应结果。
   * 若子进程尚未启动，将在此方法调用时按需懒启动。
   *
   * @param method - JSON-RPC 方法名
   * @param params - 请求参数对象
   * @returns 后端响应的解析结果 Promise
   */
  request<T>(method: string, params: object): Promise<T> {
    if (!this.child) this.start()
    const id = ++this.nextId
    let requestParams = params
    if (method === 'configure') {
      const configure = params as { options: { runtimes: { python: string } } }
      requestParams = {
        ...params,
        options: {
          ...configure.options,
          runtimes: {
            ...configure.options.runtimes,
            python: selectScriptPython(
              configure.options.runtimes.python,
              process.resourcesPath ?? path.join(this.root, 'desktop', 'resources'),
              path.join(this.root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
              this.packaged
            )
          }
        }
      }
    }
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.child!.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params: requestParams }) + '\n', error => {
        if (error) {
          this.pending.delete(id)
          reject(error)
        }
      })
    })
  }

  /**
   * 优雅关闭 Python 子进程并释放相关资源。
   */
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

