/**
 * @file agent-state.ts
 * @description 智能体交互与会话状态机纯函数模块。
 * 负责管理会话快照、流式响应文本增量累加、工具执行进度、提问交互状态、用量统计及生命周期阶段流转。
 * @author tongliga
 * @date 2026-10-16
 */

import type { EventFrame, Question, RunRecord, Snapshot } from '../../../shared/protocol'

/**
 * 智能体全局运行状态树接口契约。
 */
export type AgentState = {
  /** 当前执行生命周期阶段：已断开 / 空闲就绪 / 运行中 / 正在终止 / 等待用户答复 */
  phase: 'disconnected' | 'idle' | 'running' | 'stopping' | 'question'
  /** 当前会话的全量快照数据，包含历史消息与交付物 */
  snapshot: Snapshot | null
  /** 当前正在执行中的运行轮次阶段性统计数据 */
  liveTotals: Snapshot['totals'] | null
  /** 当前处于活跃状态的运行轮次 ID */
  runId: string | null
  /** 用户已发送但模型尚未正式响应的待决提示词文本 */
  pendingPrompt: string | null
  /** 当前 AI 正在流式生成的文本内容 */
  stream: string
  /** 正在执行或已执行工具调用记录字典（以 call_id 为键） */
  tools: Record<string, RunRecord>
  /** 最近发生的事件流水（保留最近 800 条） */
  events: RunRecord[]
  /** 本轮次非文本流事件日志（工具调用、状态转移等） */
  runLog: RunRecord[]
  /** 智能体向用户发起的等待授权或输入的问题 */
  question: Question | null
  /** 错误提示信息，为 null 表示无错误 */
  error: string | null
  /** 最近一轮运行是否已持久化保存标志 */
  lastRunSaved: boolean
  /** 最近一次成功结束的运行轮次 ID */
  lastFinishedRunId: string | null
}

/**
 * 初始空白状态对象。
 */
export const initialState: AgentState = {
  phase: 'disconnected',
  snapshot: null,
  liveTotals: null,
  runId: null,
  pendingPrompt: null,
  stream: '',
  tools: {},
  events: [],
  runLog: [],
  question: null,
  error: null,
  lastRunSaved: false,
  lastFinishedRunId: null
}

/**
 * 状态机支持处理的 Action 联合类型。
 */
type Action =
  | EventFrame
  | { event: 'run.batch'; frames: Extract<EventFrame, { event: 'run.event' }>[] }
  | { event: 'run.accepted'; run_id: string; text?: string }
  | { event: 'run.stopping' | 'error.clear' }
  | { event: 'interaction.answered'; interaction_id: string }
  | { event: 'snapshot.loaded'; data: Snapshot }

/**
 * 智能体状态转移纯函数（Reducer）。
 *
 * 第一步：根据不同的 Action 事件类别分流处理。
 * 第二步：针对流式文本增量（run.event）进行字符串高效拼接与工具状态更新。
 * 第三步：在运行完成（run.finished）时更新快照、标记保存状态并重置流式中间状态。
 * 第四步：在连接异常（backend.exited）或报错（protocol.error）时安全回退状态机。
 *
 * @param state - 当前状态快照
 * @param action - 接收到的动作载荷
 * @returns 转移后的全新不可变状态对象
 */
export function reduceAgent(state: AgentState, action: Action): AgentState {
  switch (action.event) {
    // 批量处理合并的高频流式事件，降低 React 重渲染压力
    case 'run.batch':
      return action.frames.reduce<AgentState>((current, frame) => reduceAgent(current, frame), state)

    // 清除界面展示的错误提示条
    case 'error.clear':
      return { ...state, error: null }

    // 后端服务初始化完成，若此前为未连接状态则转为空闲就绪
    case 'app.ready':
      return { ...state, phase: state.phase === 'disconnected' ? 'idle' : state.phase }

    // 会话完全就绪或加载了新的会话快照
    case 'ready':
    case 'snapshot.loaded':
      return { ...initialState, phase: 'idle', snapshot: action.data, events: action.data.active_run?.records ?? [] }

    // 用户提交的任务被后端受理接受
    case 'run.accepted':
      if (state.lastFinishedRunId === action.run_id || state.question) return state
      if (state.runId === action.run_id) return { ...state, pendingPrompt: action.text ?? state.pendingPrompt }
      return {
        ...state,
        phase: 'running',
        runId: action.run_id,
        liveTotals: null,
        pendingPrompt: action.text ?? null,
        stream: '',
        tools: {},
        events: [],
        runLog: [],
        question: null,
        error: null,
        lastRunSaved: false
      }

    // 用户发起停止当前任务
    case 'run.stopping':
      if (!state.runId) return state
      return { ...state, phase: 'stopping', question: null }

    // 用户已提交针对智能体提问的答复
    case 'interaction.answered':
      if (state.question?.interaction_id !== action.interaction_id) return state
      return { ...state, phase: 'running', question: null }

    // 接收到单条细粒度运行时事件（文本流、工具调用等）
    case 'run.event': {
      if (state.runId && state.runId !== action.run_id) return state
      const data = action.data
      const tools = data.call_id ? { ...state.tools, [data.call_id]: { ...state.tools[data.call_id], ...data } } : state.tools

      // 提取流式生成的文本片段
      const text = typeof data.text === 'string'
        ? data.text
        : Array.isArray(data.text)
          ? data.text.map(part => typeof part === 'string' ? part : typeof part === 'object' && part && 'text' in part ? String(part.text) : '').join('')
          : ''

      const stream = data.kind === 'text' ? state.stream + text : state.stream
      return {
        ...state,
        phase: state.phase === 'idle' || state.phase === 'disconnected' ? 'running' : state.phase,
        runId: action.run_id,
        liveTotals: data.totals ?? state.liveTotals,
        snapshot: data.token_usage && state.snapshot ? { ...state.snapshot, token_usage: data.token_usage } : state.snapshot,
        tools,
        stream,
        events: [...state.events.slice(-799), data],
        runLog: data.kind === 'text' ? state.runLog : [...state.runLog, data]
      }
    }

    // 智能体向用户发起交互询问（需要确认或补充信息）
    case 'interaction.request':
      return state.phase === 'stopping'
        ? state
        : { ...state, phase: 'question', runId: action.run_id, question: action.data }

    // 本轮任务执行结束并已保存快照
    case 'run.finished':
      if (state.runId && state.runId !== action.run_id) return state
      return {
        ...state,
        phase: 'idle',
        snapshot: action.data.snapshot,
        liveTotals: null,
        runId: null,
        pendingPrompt: null,
        question: null,
        lastRunSaved: true,
        lastFinishedRunId: action.run_id,
        stream: '',
        tools: {},
        runLog: [...state.runLog, { kind: 'run_finished', status: action.data.status }],
        error: action.data.status === 'failed' ? '任务失败；终态已保存。' : null
      }

    // 后端进程异常退出或断开连接
    case 'backend.exited':
      return { ...state, phase: 'disconnected', runId: null, question: null, error: action.data.message }

    // 协议层报错
    case 'protocol.error':
      return { ...state, error: action.data.message }
  }
}
