/**
 * @file App.tsx
 * @description MedPeer 桌面端主界面应用外壳组件。
 * 负责全局主题管理、布局导航条（App Rail）、侧边历史会话栏、任务主对话区、
 * 任务输入框（Composer）、多功能右侧详情抽屉（过程/目录/研究）以及全局偏好设置视图的统筹编排。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useRef, useState } from 'react'
import {
  Activity, ArrowRight, ChevronLeft, ChevronRight, CircleHelp,
  FileText, Folder, FolderOpen, Menu, Paperclip, PanelRightClose, PanelRightOpen,
  Send, Settings2, Sparkles, Square, X, House, History, Wrench, Plus
} from 'lucide-react'
import { useAgentController } from './state/use-agent-controller'
import type { Json } from '../../shared/protocol'
import { Decision } from './components/Decision'
import { Conversation } from './components/Conversation'
import { SessionSidebar } from './components/SessionSidebar'
import { CapabilityPicker, isCapabilityShortcut } from './components/CapabilityPicker'
import type { CapabilityPickerHandle } from './components/CapabilityPicker'
import { RunDetails } from './components/RunDetails'
import { ToolCard } from './components/ToolCard'
import { WorkspaceDirectory } from './components/WorkspaceDirectory'
import { UserConfig } from './plugins/user_config'
import { AttachmentChips } from './components/AttachmentChips'
import { TokenWindowPopup } from './components/TokenWindowPopup'
import { LoginScreen } from './features/auth/LoginScreen'
import { AccountMenu } from './features/auth/AccountMenu'
import './settings.css'
import './v2.css'
import './v3.css'
import './styles/layout.css'

/** 界面外观模式 */
type Theme = 'light' | 'dark'

/** 右侧详情抽屉标签页类别 */
type DetailTab = 'activity' | 'directory' | 'research' | 'files'

/** 设置面板标签页类别 */
type SettingsTab = 'models' | 'agent' | 'mcp' | 'plugins' | 'profile' | 'credits' | 'usage'

/**
 * 首次进入或空任务时的引导快捷模板列表。
 */
const starters = [
  { title: '梳理资料', note: '从文献和文件中整理事实与证据', text: '请梳理当前项目中的研究资料，明确关键结论、对应证据和待核实内容。' },
  { title: '制定方案', note: '把研究问题拆解为可执行的步骤', text: '针对当前项目，先给出分步骤的研究计划、所需数据和预期产出，等待我确认后执行。' },
  { title: '审查结果', note: '找出漏洞、局限及下一步工作', text: '请审查当前项目已有成果，找出方法、证据和结论方面的不足，提出修订建议。' }
]

/**
 * 读取本地持久化的主题设置，默认浅色主题。
 *
 * @returns 选中的主题外观
 */
function savedTheme(): Theme {
  try {
    return localStorage.getItem('medpeer.theme') === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/**
 * MedPeer 桌面应用根界面组件。
 */
export default function App() {
  // 第一步：接入核心智能体控制器 Hook
  const c = useAgentController()
  const {
    state, application, project, workspace, draft, setDraft, attachments, setAttachments, sessions,
    sessionSearch, searchSessions, config, error, clearError, busy,
    chooseWorkspace, loadSession, send, stop, answer, openArtifact
  } = c

  // 第二步：UI 布局与显示状态管理
  const [theme, setTheme] = useState<Theme>(savedTheme)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailTab, setDetailTab] = useState<DetailTab>('activity')
  const [view, setView] = useState<'task' | 'settings'>('task')
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('models')
  const [dragging, setDragging] = useState(false)
  const [detailWidth, setDetailWidth] = useState(340)
  const [selectedPlugins, setSelectedPlugins] = useState<string[]>([])
  const [selectedSkills, setSelectedSkills] = useState<string[]>([])
  useEffect(() => {
    setSelectedPlugins([])
    setSelectedSkills([])
  }, [project?.id, state.snapshot?.session_id])
  const submitTask = async () => {
    if (await send(selectedPlugins, selectedSkills)) {
      setSelectedPlugins([])
      setSelectedSkills([])
    }
  }

  const isResizing = useRef(false)
  const dragDepth = useRef(0)
  const composer = useRef<HTMLTextAreaElement>(null)
  const capabilityPicker = useRef<CapabilityPickerHandle>(null)
  const search = useRef<HTMLInputElement>(null)

  // 处理任务详情面板宽度的鼠标拖拽调节事件
  const handleResizeStart = (event: React.MouseEvent) => {
    event.preventDefault()
    isResizing.current = true
    const startX = event.clientX
    const startWidth = detailWidth
    const onMouseMove = (moveEvent: MouseEvent) => {
      if (!isResizing.current) return
      const delta = startX - moveEvent.clientX
      // 限制拖拉宽度：最小 260px，最大为屏幕宽度的 70%
      const next = Math.max(260, Math.min(Math.floor(window.innerWidth * 0.7), startWidth + delta))
      setDetailWidth(next)
    }
    const onMouseUp = () => {
      isResizing.current = false
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }

  // 派生计算界面状态
  const connected = application !== null && state.phase !== 'disconnected'
  const modelReady = !!application?.user
  const canCompose = connected && modelReady && !!project
  const sendDisabled = busy || !canCompose || !draft.trim()
  const title = sessions.find(item => item.id === state.snapshot?.session_id)?.title ??
    (state.snapshot?.messages.length ? '当前任务' : '新任务')
  const activityCount = Object.keys(state.tools).length

  // 当前任务状态描述文本
  const status = state.phase === 'disconnected' ? '连接已断开'
    : state.phase === 'question' ? '等待确认'
    : state.phase === 'stopping' ? '正在停止'
    : state.phase === 'running' ? '正在工作'
    : !project || !modelReady ? '待配置'
    : '就绪'

  // 同步主题至文档根元素
  useEffect(() => {
    document.documentElement.style.colorScheme = theme
    try { localStorage.setItem('medpeer.theme', theme) } catch { /* 忽略本地存储禁用异常 */ }
  }, [theme])

  // 全局快捷键注册（Ctrl/Cmd + K 聚焦搜索，Ctrl/Cmd + N 新任务，Ctrl/Cmd + , 打开设置）
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      const mod = event.ctrlKey || event.metaKey
      if (mod && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setView('task')
        setSidebarOpen(true)
        requestAnimationFrame(() => search.current?.focus())
      } else if (mod && event.key.toLowerCase() === 'n') {
        event.preventDefault()
        if (!busy && connected) {
          setView('task')
          void loadSession('session.clear')
        }
      } else if (mod && event.key === ',') {
        event.preventDefault()
        setView(old => old === 'settings' ? 'task' : 'settings')
      } else if (mod && event.shiftKey && event.key.toLowerCase() === 'e') {
        event.preventDefault()
        setView('task')
        setDetailTab('activity')
        setDetailOpen(old => !old)
      } else if (event.key === 'Escape') {
        setDetailOpen(false)
        setDragging(false)
        dragDepth.current = 0
      }
    }
    addEventListener('keydown', shortcut)
    return () => removeEventListener('keydown', shortcut)
  }, [busy, connected, loadSession])

  // 监听插件打开工作台事件
  useEffect(() => {
    const openWorkbench = () => {
      setView('task')
      setDetailTab('research')
      setDetailOpen(true)
    }
    addEventListener('medpeer:open-workbench', openWorkbench)
    return () => removeEventListener('medpeer:open-workbench', openWorkbench)
  }, [])

  /** 检验拖拽项是否为本地文件传输 */
  function isFileDrag(event: React.DragEvent) {
    return [...event.dataTransfer.types].includes('Files')
  }

  /** 打开指定页签的设置界面 */
  function openSettings(tab: SettingsTab = 'models') {
    setSettingsTab(tab)
    setView('settings')
  }

  // 若未登录则直接展示全屏登录页面
  if (application && !application.user) {
    return <LoginScreen theme={theme} />
  }

  return <div
    className={`app product-v2 product-v3 ${theme}`}
    onDragEnterCapture={event => {
      if (!isFileDrag(event)) return
      event.preventDefault()
      dragDepth.current++
      setDragging(view === 'task' && !!(event.target as Element).closest('.task-main'))
    }}
    onDragOverCapture={event => {
      if (!isFileDrag(event)) return
      event.preventDefault()
      setDragging(view === 'task' && !!(event.target as Element).closest('.task-main'))
    }}
    onDragLeaveCapture={event => {
      if (!isFileDrag(event)) return
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (!dragDepth.current) setDragging(false)
    }}
    onDropCapture={event => {
      if (!isFileDrag(event)) return
      event.preventDefault()
      dragDepth.current = 0
      setDragging(false)
    }}
    onDrop={event => {
      if (!isFileDrag(event)) return
      if (view !== 'task' || !(event.target as Element).closest('.task-main')) return
      if (!canCompose || busy) {
        c.reportError('请先登录并选择项目；运行任务时不可添加文件。')
        return
      }
      void c.importDroppedFiles([...event.dataTransfer.files])
    }}
  >
    <div
      className={`workspace-layout ${sidebarOpen && view === 'task' ? 'has-sidebar' : ''} ${detailOpen && view === 'task' ? 'has-detail' : ''} ${view === 'settings' ? 'view-settings' : ''}`}
      style={{ '--detail-width': `${detailWidth}px` } as React.CSSProperties}
    >
      {/* 全局左侧极窄导航栏 (App Rail) */}
      <nav className="app-rail" aria-label="全局导航">
        <button
          className={view === 'task' ? 'rail-nav active' : 'rail-nav'}
          aria-label="任务"
          title="任务"
          onClick={() => setView('task')}
        >
          <House size={19} />
        </button>
        <button
          className="rail-nav"
          aria-label="新建任务"
          title="新建任务"
          disabled={busy || !connected}
          onClick={() => { setView('task'); void loadSession('session.clear') }}
        >
          <Plus size={20} />
        </button>
        <button
          className="rail-nav"
          aria-label="搜索历史"
          title="搜索历史 Ctrl+K"
          onClick={() => { setView('task'); setSidebarOpen(true); requestAnimationFrame(() => search.current?.focus()) }}
        >
          <History size={19} />
        </button>
        <span className="rail-fill" />
        <button
          className={`rail-nav ${view === 'settings' ? 'active' : ''}`}
          aria-label="设置"
          title="设置 Ctrl+,"
          onClick={() => openSettings()}
        >
          <Settings2 size={19} />
        </button>
        {application?.user && <AccountMenu user={application.user} busy={busy} onError={c.reportError} />}
      </nav>

      {/* 历史会话与项目侧边栏 */}
      {sidebarOpen && view === 'task' && <SessionSidebar
        sessions={sessions}
        search={sessionSearch}
        searchInputRef={search}
        onSearch={query => void searchSessions(query)}
        currentId={state.snapshot?.session_id}
        projectId={application?.active_project_id ?? null}
        projects={application?.projects ?? []}
        busy={busy}
        connected={connected}
        onChooseWorkspace={chooseWorkspace}
        onSelectProject={id => void c.applyApplication('project.select', { project_id: id })}
        onResume={id => { setView('task'); void loadSession('session.resume', id) }}
        onClear={() => { setView('task'); void loadSession('session.clear') }}
        onRename={(id, text) => void c.renameSession(id, text)}
        onDelete={id => void c.deleteSession(id)}
        onExport={id => void c.exportSession(id)}
        onOpenSettings={() => openSettings()}
        theme={theme}
        onToggleTheme={() => setTheme(old => old === 'dark' ? 'light' : 'dark')}
      />}

      {/* 任务主工作区 */}
      <main className="task-main">
        {/* 顶部标题栏 */}
        <header className="task-header">
          <div className="task-header-left">
            <button
              className="icon-action"
              title={sidebarOpen ? '隐藏侧边栏' : '显示侧边栏'}
              aria-label="切换侧边栏"
              onClick={() => setSidebarOpen(old => !old)}
            >
              <Menu size={19} />
            </button>
            {view === 'settings' ? <>
              <span className="header-path">设置</span>
              <ChevronRight size={13} className="muted-icon" />
              <strong>偏好与连接</strong>
            </> : <>
              <span className="header-path" title={project?.path ?? project?.name ?? ''}>
                {project?.name ?? '未选择项目'}
              </span>
              <ChevronRight size={13} className="muted-icon" />
              <strong title={title}>{title}</strong>
            </>}
          </div>
          <div className="task-header-right">
            {view === 'settings' ? <button className="subtle-button" onClick={() => setView('task')}>
              <ChevronLeft size={15} />返回任务
            </button> : <>
              <span className={`task-status ${state.phase}`} role="status">
                <span className="status-indicator" />{status}
              </span>
              <button
                className="icon-action"
                title={detailOpen ? '收起执行详情' : '查看执行详情'}
                aria-label="切换执行详情"
                aria-expanded={detailOpen}
                onClick={() => { setDetailTab('activity'); setDetailOpen(old => !old) }}
              >
                {detailOpen ? <PanelRightClose size={19} /> : <PanelRightOpen size={19} />}
              </button>
            </>}
            {!sidebarOpen && <button className="icon-action" aria-label="打开设置" title="设置" onClick={() => openSettings()}>
              <Settings2 size={18} />
            </button>}
          </div>
        </header>

        {/* 视图主体内容：设置页面 vs 任务对话区 */}
        {view === 'settings' ? <UserConfig
          key={settingsTab}
          initialTab={settingsTab}
          application={application}
          config={config}
          busy={busy}
          apply={c.applyApplication}
          inspect={c.inspectConfig}
          save={c.saveConfig}
          remove={c.removeConfig}
          toggleSkill={c.toggleSkill}
          testMcp={c.testMcp}
          importSkills={c.importSkills}
          testProfile={c.testProfile}
          readSkill={c.readSkill}
          saveSkill={c.saveSkill}
        /> : <>
          {/* 对话列表或空任务引导卡片 */}
          {state.snapshot?.messages.length || state.pendingPrompt || state.stream.length || activityCount || busy ? (
            <Conversation state={state} openArtifact={openArtifact} />
          ) : (
            <section className="task-empty">
              <div className="empty-inner">
                <h1>{!connected ? '工作台暂不可用' : !modelReady ? '登录 MedPeer' : !project ? '选择一个项目' : '从一个任务开始'}</h1>
                <p>{!connected ? '本地服务尚未就绪。检查启动日志与 Python 环境。' : !modelReady ? '登录 MedPeer 账户以使用内置模型。' : !project ? '在左侧创建项目，或打开已有的本地文件夹。' : '描述目标即可开始。执行过程和交付文件会保留在当前项目下。'}</p>
                {!modelReady && connected ? (
                  <button className="primary-action empty-cta" onClick={() => openSettings('models')}>
                    登录 MedPeer <ArrowRight size={15} />
                  </button>
                ) : !project && connected ? (
                  <button className="primary-action empty-cta" onClick={() => setSidebarOpen(true)}>
                    选择项目 <ArrowRight size={15} />
                  </button>
                ) : canCompose && (
                  <div className="starter-list">
                    {starters.map(item => <button key={item.title} onClick={() => {
                      setDraft(item.text)
                      requestAnimationFrame(() => composer.current?.focus())
                    }}>
                      <span><strong>{item.title}</strong><small>{item.note}</small></span>
                      <ArrowRight size={16} />
                    </button>)}
                  </div>
                )}
              </div>
            </section>
          )}

          {/* 任务底部输入区域 */}
          <div className="task-bottom">
            {/* 提问交互卡片 */}
            {state.question && state.runId && <Decision
              key={state.question.interaction_id}
              kind={state.question.kind}
              pluginId={state.question.plugin_id}
              pluginName={application?.plugins.find(plugin => plugin.id === state.question!.plugin_id)?.name}
              question={state.question.question}
              actionRequests={state.question.action_requests}
              options={state.question.options}
              purpose={state.question.purpose}
              inputScope={state.question.input_scope}
              plannedOutputs={state.question.planned_outputs}
              allowedEffects={state.question.allowed_effects}
              onAnswer={value => answer(state.runId!, state.question!.interaction_id, value)}
              onStop={() => void stop()}
            />}

            {/* 执行进度条 */}
            {state.phase === 'running' && <div className="work-progress" role="status">
              <span className="work-spinner" />
              正在执行任务{activityCount ? ` · ${activityCount} 个工具步骤` : ''}
              <button onClick={() => { setDetailOpen(true); setDetailTab('activity') }}>
                查看过程 <ChevronRight size={13} />
              </button>
            </div>}

            {/* 输入文本域与工具栏 */}
            <div className={`task-composer ${!canCompose ? 'not-ready' : ''}`}>
              {attachments.length > 0 && <AttachmentChips
                attachments={attachments}
                remove={id => setAttachments(old => old.filter(item => item.id !== id))}
                onError={c.reportError}
              />}
              <textarea
                ref={composer}
                aria-label="任务输入"
                value={draft}
                disabled={busy || !canCompose}
                rows={2}
                placeholder={!connected ? '本地服务未连接…' : !modelReady ? '先登录 MedPeer…' : !project ? '先选择项目…' : '描述你希望完成的任务…'}
                onChange={event => {
                  const val = event.target.value
                  setDraft(val)
                  // 若用户键入以 '/' 开头的斜杠指令，自动唤起命令与扩展能力浮层
                  if (val.startsWith('/')) {
                    if (!capabilityPicker.current?.isOpen()) {
                      capabilityPicker.current?.open(val.slice(1))
                    }
                  } else if (!val.trim() && capabilityPicker.current?.isOpen()) {
                    capabilityPicker.current?.close()
                  }
                }}
                onKeyDown={event => {
                  // 第一步：若能力选择浮层已开启，优先交由浮层处理方向键、回车与退出
                  if (capabilityPicker.current?.isOpen()) {
                    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter') {
                      if (capabilityPicker.current.handleKeyDown?.(event)) {
                        return
                      }
                    }
                    if (event.key === 'Escape') {
                      event.preventDefault()
                      capabilityPicker.current.close()
                      if (draft.startsWith('/')) setDraft('')
                      return
                    }
                  }
                  // 第二步：空输入框键入 '/' 快捷唤起能力浮层
                  if (isCapabilityShortcut(event) && !draft.trim()) {
                    capabilityPicker.current?.open('')
                    return
                  }
                  // 第三步：普通回车触发任务提交
                  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault()
                    if (!sendDisabled) void submitTask()
                  }
                }}
              />
              <div className="composer-bottom-row">
                <div className="composer-context">
                  <button
                    className="attach-control"
                    disabled={!canCompose || busy}
                    aria-label="添加文件"
                    title="添加文件"
                    onClick={() => void c.importAttachments()}
                  >
                    <Paperclip size={17} />
                  </button>
                  <span className="context-sep" />
                  <span className="composer-model" title="MedPeer 统一模型">
                    <Sparkles size={13}/>{application?.model?.model ?? '未连接模型'}
                  </span>
                  <span className="context-sep" />
                  <CapabilityPicker
                    ref={capabilityPicker}
                    application={application}
                    plugins={selectedPlugins}
                    skills={selectedSkills}
                    setPlugins={setSelectedPlugins}
                    setSkills={setSelectedSkills}
                    disabled={busy || !canCompose}
                    onEscape={() => composer.current?.focus()}
                    externalQuery={draft.startsWith('/') ? draft.slice(1) : ''}
                    onSelectPrompt={prompt => {
                      setDraft(prompt)
                      requestAnimationFrame(() => composer.current?.focus())
                    }}
                    onCapabilityApplied={() => {
                      if (draft.startsWith('/')) {
                        setDraft('')
                      }
                      requestAnimationFrame(() => composer.current?.focus())
                    }}
                    onClearSession={() => {
                      setView('task')
                      void loadSession('session.clear')
                      if (draft.startsWith('/')) setDraft('')
                    }}
                    onToggleDetail={() => {
                      setDetailTab('activity')
                      setDetailOpen(true)
                      if (draft.startsWith('/')) setDraft('')
                    }}
                    onOpenSettings={tab => {
                      openSettings(tab as SettingsTab)
                      if (draft.startsWith('/')) setDraft('')
                    }}
                  />
                  <span className="context-sep" />
                  <button
                    className="composer-tool-hint"
                    disabled={!canCompose}
                    onClick={() => {
                      if (capabilityPicker.current?.isOpen()) {
                        capabilityPicker.current.close()
                      } else {
                        capabilityPicker.current?.open()
                      }
                    }}
                    title="选择扩展插件与技能 (/)"
                  >
                    <Wrench size={14} />插件
                  </button>
                </div>
                <div className="composer-actions">
                  <TokenWindowPopup report={state.snapshot?.token_usage} onOpenSettings={() => openSettings('models')} />
                  {busy ? <button className="submit-task stop-task" aria-label="停止任务" disabled={state.phase === 'stopping'} onClick={() => void stop()}>
                    <Square size={15} fill="currentColor" />
                  </button> : <button className="submit-task" aria-label="提交任务" disabled={sendDisabled} onClick={() => void submitTask()}>
                    <Send size={16} />
                  </button>}
                </div>
              </div>
            </div>
            <div className="task-footnote">
              <span>Enter 提交 · Shift + Enter 换行</span>
              <span>{workspace ? `已关联 ${workspace.split(/[\\/]/).at(-1)}` : '项目文件与会话保存在本机'}</span>
            </div>
          </div>
        </>}

        {/* 拖入文件高亮蒙层 */}
        {dragging && <div className="drop-overlay">
          <div>
            <FolderOpen size={30} />
            <strong>松开以添加对话附件</strong>
            <span>文件将附加到当前任务</span>
          </div>
        </div>}
      </main>

      {/* 右侧执行详情/目录/插件抽屉 */}
      {detailOpen && view === 'task' && <aside className="task-detail" aria-label="任务详情" style={{ width: `${detailWidth}px` }}>
        <div className="task-detail-resizer" title="拖拉调节面板宽度" onMouseDown={handleResizeStart} />
        <header className="detail-header">
          <strong>任务详情</strong>
          <button className="icon-action" aria-label="关闭任务详情" onClick={() => setDetailOpen(false)}>
            <X size={17} />
          </button>
        </header>
        <div className="detail-tabs" role="tablist" aria-label="详情类型">
          <button role="tab" aria-selected={detailTab === 'activity'} onClick={() => setDetailTab('activity')}>
            <Activity size={14} />过程
          </button>
          <button role="tab" aria-selected={detailTab === 'directory' || detailTab === 'files'} onClick={() => setDetailTab('directory')}>
            <Folder size={14} />目录
          </button>
          <button role="tab" aria-selected={detailTab === 'research'} onClick={() => setDetailTab('research')}>
            <CircleHelp size={14} />研究
          </button>
        </div>
        <div className="detail-body">
          {detailTab === 'activity' ? <>
            <RunDetails state={state} />
            <section className="detail-section">
              <h3>工具执行记录</h3>
              {Object.entries(state.tools).length ? Object.entries(state.tools).map(([id, record]) => (
                <ToolCard key={id} callId={id} record={record} />
              )) : (
                <p className="detail-empty">当前没有正在进行的工具调用。历史工具参数和结果见上方已保存运行。</p>
              )}
            </section>
          </> : (detailTab === 'directory' || detailTab === 'files') ? (
            <WorkspaceDirectory workspace={workspace} onChooseWorkspace={() => void chooseWorkspace()} lastRunFinished={state.lastRunSaved} />
          ) : (
            <section className="detail-section">
              <h3>研究扩展</h3>
              {(application?.plugins ?? []).map(plugin => <article className="plugin-card" key={plugin.id}>
                <strong>{plugin.name}</strong><span>{plugin.loaded ? '已加载' : plugin.enabled ? '已启用' : '已停用'}</span>
                <p>{plugin.description}</p>
                {plugin.workflows.length > 0 && <ul>{plugin.workflows.map(workflow => <li key={workflow.name}><strong>{workflow.name}</strong> · {workflow.description}</li>)}</ul>}
              </article>)}
              {!application?.plugins.length && <p className="detail-empty">尚未发现可用插件。</p>}
            </section>
          )}
        </div>
      </aside>}
    </div>

    {/* 错误浮层条 */}
    {(error || state.error) && <div className="error-banner" role="alert">
      <span>{error || state.error}</span>
      <button aria-label="关闭错误提示" onClick={clearError}>
        <X size={16} />
      </button>
    </div>}
  </div>
}
