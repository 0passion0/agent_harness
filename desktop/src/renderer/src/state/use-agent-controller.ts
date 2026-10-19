/**
 * @file use-agent-controller.ts
 * @description React 渲染层核心状态与通信控制 Hook。
 * 整合后端推送事件流批处理（通过 requestAnimationFrame 防抖合并）、
 * 智能体生命周期状态机驱动、会话历史检索与管理、项目切换以及配置读写。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useReducer, useRef, useState } from 'react'
import type { ApplicationState, AttachmentRef, ConfigScope, ConfigView, EventFrame, SessionItem, Snapshot } from '../../../shared/protocol'
import { initialState, reduceAgent } from './agent-state'

/**
 * 生成随机 UUID 请求标识符辅助函数。
 */
const requestId = () => crypto.randomUUID()

/**
 * 智能体桌面应用主控制器 Hook。
 *
 * 第一步：初始化状态机（useReducer + reduceAgent）与各项受控状态（草稿、附件、会话等）。
 * 第二步：设置全局事件帧监听，使用 requestAnimationFrame 对高频 run.event 进行批处理合并。
 * 第三步：封装会话检索、插件快照读取与防抖刷新机制。
 * 第四步：封装发送提示词（send）、停止任务（stop）、答复确认（answer）等用户操作指令。
 * 第五步：暴露系统配置、技能管理、MCP 调试与本地附件导入接口。
 *
 * @returns 包含状态树及所有交互操作方法的控制器对象
 */
export function useAgentController() {
  // 第一步：核心状态机与局部受控状态声明
  const [state, dispatch] = useReducer(reduceAgent, initialState)
  const [application, setApplication] = useState<ApplicationState | null>(null)
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<AttachmentRef[]>([])
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [sessionSearch, setSessionSearch] = useState('')
  const [config, setConfig] = useState<ConfigView | null>(null)
  const [error, setError] = useState('')

  // 引用计数与竞态保护控制器
  const queryVersion = useRef(0)
  const sessionSearchVersion = useRef(0)
  const sessionSearchQuery = useRef('')
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const sendPending = useRef(false)
  const activeSession = useRef<string | null>(null)
  const [sending, setSending] = useState(false)

  // 派生状态：是否处于忙碌（任务运行中、等待答复或正在发送）
  const busy = sending || state.phase === 'running' || state.phase === 'stopping' || state.phase === 'question'
  // 当前激活的项目信息与本地工作区物理路径
  const project = application?.projects.find(item => item.id === application.active_project_id) ?? null
  const workspace = project?.path ?? ''

  /**
   * 刷新当前项目下的历史会话列表（支持按关键词过滤）。
   */
  async function refreshList(): Promise<void> {
    const version = ++sessionSearchVersion.current
    const result = await window.medpeer.command({
      id: requestId(),
      method: 'session.search',
      params: { query: sessionSearchQuery.current }
    }) as SessionItem[]
    // 竞态保护：仅当返回版本与当前最新版本一致时才提交更新
    if (version === sessionSearchVersion.current) setSessions(result)
  }

  /**
   * 带防抖的会话搜索输入处理函数。
   *
   * @param query - 搜索关键字
   */
  async function searchSessions(query: string): Promise<void> {
    sessionSearchQuery.current = query
    setSessionSearch(query)
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      void refreshList().catch(reason => setError(String(reason)))
    }, 220)
  }

  /**
   * 初始化或重新加载后端应用与会话快照数据。
   */
  async function retryStartup(): Promise<void> {
    setError('')
    try {
      const [appValue, sessionValue] = await Promise.all([
        window.medpeer.command({ id: requestId(), method: 'app.snapshot', params: {} }),
        window.medpeer.command({ id: requestId(), method: 'session.snapshot', params: {} })
      ])
      const data = appValue as ApplicationState
      const snapshot = sessionValue as Snapshot
      setApplication(data)
      activeSession.current = snapshot.session_id
      dispatch({ event: 'app.ready', data })
      dispatch({ event: 'snapshot.loaded', data: snapshot })
      await refreshList()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  // 第二步：注册后端事件帧监听，建立高频帧 rAF 合并调度
  useEffect(() => {
    type RunFrame = Extract<EventFrame, { event: 'run.event' }>
    let frames: RunFrame[] = []
    let scheduled: number | null = null

    // 刷新暂存队列并派发给 Reducer
    const flush = () => {
      if (scheduled !== null) cancelAnimationFrame(scheduled)
      scheduled = null
      if (frames.length) {
        dispatch({ event: 'run.batch', frames })
        frames = []
      }
    }

    const unsubscribe = window.medpeer.onEvent(event => {
      // 对文本流增量与高频工具事件进行缓冲合并，防止极短时间内触发过多 React 渲染
      if (event.event === 'run.event') {
        frames.push(event)
        if (scheduled === null) scheduled = requestAnimationFrame(flush)
        return
      }

      // 其他控制事件先清空当前缓冲区，再同步处理
      flush()
      dispatch(event)

      // 应用全量状态初始化
      if (event.event === 'app.ready') {
        setApplication(event.data)
        refreshList().catch(reason => setError(String(reason)))
        return
      }

      // 会话重置或切入新会话
      if (event.event === 'ready') {
        activeSession.current = event.data.session_id
        refreshList().catch(reason => setError(String(reason)))
      }

      // 单轮任务执行结束
      if (event.event === 'run.finished') {
        activeSession.current = event.data.snapshot.session_id
        refreshList().catch(reason => setError(String(reason)))
      }

      // 后端进程异常退出
      if (event.event === 'backend.exited') {
        queryVersion.current++
        activeSession.current = null
      }
    })

    // 初始化时主动拉取应用快照与当前会话快照
    void retryStartup()

    return () => {
      unsubscribe()
      if (scheduled !== null) cancelAnimationFrame(scheduled)
      frames = []
      if (searchTimer.current) clearTimeout(searchTimer.current)
    }
  }, [])

  // 第三步：应用级别全局配置变更封装
  async function applyApplication(method: 'project.create', params: { name: string }): Promise<void>
  async function applyApplication(method: 'project.select', params: { project_id: string }): Promise<void>
  async function applyApplication(method: 'model.test' | 'app.snapshot', params: Record<string, never>): Promise<void>
  async function applyApplication(method: 'model.configure', params: { model_code: string }): Promise<void>
  async function applyApplication(method: 'context.configure', params: { max_tokens: string }): Promise<void>
  async function applyApplication(method: 'permission.set', params: { mode: ApplicationState['permission_mode'] }): Promise<void>
  async function applyApplication(method: string, params: object): Promise<void> {
    setError('')
    try {
      setApplication(await window.medpeer.command({ id: requestId(), method, params } as never) as ApplicationState)
      if (method === 'project.select' || method === 'project.create') await refreshList()
    }
    catch (reason) {
      setError(String(reason))
      if (method === 'model.configure') throw reason
    }
  }

  /**
   * 调起系统原生对话框选择本地工作区目录并绑定到当前项目。
   */
  async function chooseWorkspace(): Promise<void> {
    setError('')
    try {
      await window.medpeer.selectWorkspace()
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 清空或恢复特定历史会话。
   *
   * @param method - 清空会话或恢复指定会话
   * @param sessionId - 恢复时的目标会话标识符
   */
  async function loadSession(method: 'session.clear' | 'session.resume', sessionId?: string): Promise<void> {
    setError('')
    try {
      const snapshot = await window.medpeer.command(method === 'session.clear'
        ? { id: requestId(), method, params: {} }
        : { id: requestId(), method, params: { session_id: sessionId! } }) as Snapshot
      activeSession.current = snapshot.session_id
      queryVersion.current++
      dispatch({ event: 'snapshot.loaded', data: snapshot })
      await refreshList()
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 向智能体提交新任务指令。
   * 支持纯文本提示词以及附加本地文件。
   */
  async function send(selectedPlugins: string[], selectedSkills: string[]): Promise<boolean> {
    if (busy || sendPending.current || !project || !application?.model || !draft.trim()) return false
    sendPending.current = true
    setSending(true)
    const id = requestId()
    const text = draft
    const selectedAttachments = [...attachments]
    setError('')
    try {
      await window.medpeer.command(selectedAttachments.length
        ? { id, method: 'run.attachments', params: { text, attachment_ids: selectedAttachments.map(file => file.id), selected_plugins: selectedPlugins, selected_skills: selectedSkills } }
        : { id, method: 'run.start', params: { text, selected_plugins: selectedPlugins, selected_skills: selectedSkills } })
      dispatch({ event: 'run.accepted', run_id: id, text })
      setDraft(current => current === text ? '' : current)
      const submitted = new Set(selectedAttachments.map(item => item.id))
      setAttachments(current => current.filter(file => !submitted.has(file.id)))
      return true
    } catch (reason) {
      setError(String(reason))
      return false
    } finally {
      sendPending.current = false
      setSending(false)
    }
  }

  /**
   * 中断并强制停止当前正在运行的智能体任务。
   */
  async function stop(): Promise<void> {
    if (!state.runId) return
    try {
      await window.medpeer.command({ id: requestId(), method: 'run.stop', params: { run_id: state.runId } })
      dispatch({ event: 'run.stopping' })
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 提交对智能体交互提问的答复。
   *
   * @param runId - 任务轮次 ID
   * @param interactionId - 提问交互 ID
   * @param text - 答复内容（如 'approve' 或用户输入文本）
   */
  async function answer(runId: string, interactionId: string, text: string): Promise<void> {
    if (state.phase !== 'question' || state.runId !== runId || state.question?.interaction_id !== interactionId) return
    try {
      await window.medpeer.command({
        id: requestId(),
        method: 'interaction.answer',
        params: { run_id: runId, interaction_id: interactionId, answer: text }
      })
      dispatch({ event: 'interaction.answered', interaction_id: interactionId })
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 检查并拉取智能体规则、技能及 MCP 配置。
   */
  async function inspectConfig(scope: ConfigScope): Promise<void> {
    try {
      setConfig(await window.medpeer.command({ id: requestId(), method: 'config.inspect', params: { scope } }) as ConfigView)
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 保存智能体指令或 MCP 配置。
   */
  async function saveConfig(scope: ConfigScope, kind: 'agent' | 'mcp', content: string): Promise<void> {
    try {
      setConfig(await window.medpeer.command({ id: requestId(), method: 'config.save', params: { scope, kind, content } }) as ConfigView)
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 移除当前项目的指令或 MCP 局部覆盖项。
   */
  async function removeConfig(kind: 'agent' | 'mcp'): Promise<void> {
    try {
      setConfig(await window.medpeer.command({ id: requestId(), method: 'config.remove', params: { kind } }) as ConfigView)
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 启用或停用指定的 Skill 技能。
   */
  async function toggleSkill(scope: ConfigScope, id: string, enabled: boolean): Promise<void> {
    try {
      setConfig(await window.medpeer.command({
        id: requestId(),
        method: 'skill.set',
        params: { scope, skill_id: id, enabled: String(enabled) as 'true' | 'false' }
      }) as ConfigView)
      await applyApplication('app.snapshot', {})
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 发起指定 MCP 服务的连通性测试。
   */
  async function testMcp(scope: ConfigScope, id: string): Promise<void> {
    try {
      setError(JSON.stringify(await window.medpeer.command({ id: requestId(), method: 'mcp.test', params: { scope, server_id: id } })))
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 测试当前绑定模型的连通性与可用性。
   */
  async function testProfile(): Promise<string> {
    const result = await window.medpeer.command({ id: requestId(), method: 'model.test', params: {} }) as ApplicationState
    setApplication(result)
    return `模型连接测试成功 · ${result.model!.model}`
  }

  /**
   * 弹出目录选择框导入自定义 Skill 目录。
   */
  async function importSkills(scope: ConfigScope): Promise<void> {
    try {
      const result = await window.medpeer.importSkill(scope)
      if (result) {
        setConfig(result)
        await applyApplication('app.snapshot', {})
      }
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 调起系统文件选择框导入对话附件。
   */
  async function importAttachments(): Promise<void> {
    try {
      const files = await window.medpeer.importAttachments()
      setAttachments(current => [...current, ...files])
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 接收外部拖拽放入的文件并导入为对话附件。
   */
  async function importDroppedFiles(files: File[]): Promise<void> {
    if (!project) return
    try {
      const imported = await window.medpeer.importDroppedFiles(files)
      setAttachments(current => [...current, ...imported])
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 读取指定 Skill 的 Markdown 文件正文。
   */
  async function readSkill(scope: ConfigScope, id: string): Promise<{ content: string; source: string } | null> {
    try {
      return await window.medpeer.command({ id: requestId(), method: 'skill.read', params: { scope, skill_id: id } }) as { content: string; source: string }
    } catch (reason) {
      setError(String(reason))
      return null
    }
  }

  /**
   * 保存对指定 Skill 内容的编辑。
   */
  async function saveSkill(scope: ConfigScope, id: string, content: string): Promise<void> {
    try {
      setConfig(await window.medpeer.command({ id: requestId(), method: 'skill.save', params: { scope, skill_id: id, content } }) as ConfigView)
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 调用本地程序打开交付成果物。
   */
  async function openArtifact(path: string): Promise<void> {
    try {
      await window.medpeer.openArtifact(path)
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 重命名指定会话标题。
   */
  async function renameSession(sessionId: string, title: string): Promise<void> {
    try {
      await window.medpeer.command({ id: requestId(), method: 'session.rename', params: { session_id: sessionId, title } })
      await refreshList()
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 删除指定会话，若删除的是当前正处于打开状态的会话，则自动重置为空白会话。
   */
  async function deleteSession(sessionId: string): Promise<void> {
    try {
      await window.medpeer.command({ id: requestId(), method: 'session.delete', params: { session_id: sessionId } })
      if (state.snapshot?.session_id === sessionId) {
        const snapshot = await window.medpeer.command({ id: requestId(), method: 'session.clear', params: {} }) as Snapshot
        activeSession.current = snapshot.session_id
        dispatch({ event: 'snapshot.loaded', data: snapshot })
      }
      await refreshList()
    } catch (reason) {
      setError(String(reason))
    }
  }

  /**
   * 导出会话全部历史记录为本地文件。
   */
  async function exportSession(sessionId: string): Promise<void> {
    try {
      await window.medpeer.exportSession(sessionId)
    } catch (reason) {
      setError(String(reason))
    }
  }

  // 返回外部组件消费的完整控制器对象
  return {
    state,
    application,
    project,
    workspace,
    draft,
    setDraft,
    attachments,
    setAttachments,
    sessions,
    config,
    error,
    clearError: () => { setError(''); dispatch({ event: 'error.clear' }) },
    reportError: (message: string) => setError(message),
    busy,
    chooseWorkspace,
    loadSession,
    send,
    stop,
    answer,
    openArtifact,
    applyApplication,
    inspectConfig,
    saveConfig,
    removeConfig,
    toggleSkill,
    testMcp,
    testProfile,
    importSkills,
    importAttachments,
    importDroppedFiles,
    readSkill,
    saveSkill,
    renameSession,
    deleteSession,
    exportSession,
    sessionSearch,
    searchSessions,
    retryStartup
  }
}
