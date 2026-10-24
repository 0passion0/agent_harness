/**
 * @file SessionSidebar.tsx
 * @description 历史会话与项目管理侧边栏组件。
 * 负责呈现多项目切换、新建项目、关联本地工作区目录、历史会话按更新时间智能归组
 * （今天/昨天/最近7天/更早）、会话快捷搜索过滤、就地重命名、删除及导出会话。
 * @author tongliga
 * @date 2026-10-16
 */

import { useState } from 'react'
import type { RefObject } from 'react'
import {
  Check, Download, Folder, FolderOpen, MessageSquare, MessageSquarePlus,
  MoreHorizontal, Pencil, Plus, Search, Trash2, X, Settings2, Moon, Sun
} from 'lucide-react'
import type { SessionItem } from '../../../shared/protocol'

/**
 * 侧边栏属性接口契约。
 */
type SidebarProps = {
  /** 当前项目下的所有历史会话列表 */
  sessions: SessionItem[]
  /** 当前搜索输入词 */
  search: string
  /** 搜索输入框 DOM 引用（供全局快捷键聚焦） */
  searchInputRef?: RefObject<HTMLInputElement | null>
  /** 搜索输入变动回调 */
  onSearch(query: string): void
  /** 当前正处于打开状态的会话 ID */
  currentId?: string
  /** 当前激活的项目 ID */
  projectId: string | null
  /** 用户本地已创建的所有项目列表 */
  projects: Array<{ id: string; name: string; path: string | null }>
  /** 是否处于系统忙碌状态 */
  busy: boolean
  /** 后端服务是否已连接就绪 */
  connected: boolean
  /** 触发选择本地工作区目录回调 */
  onChooseWorkspace(): void
  /** 切换激活项目回调 */
  onSelectProject(id: string): void
  /** 恢复指定历史会话回调 */
  onResume(id: string): void
  /** 重置并开启新任务会话回调 */
  onClear(): void
  /** 重命名会话标题回调 */
  onRename(id: string, title: string): void
  /** 删除会话回调 */
  onDelete(id: string): void
  /** 导出会话文本回调 */
  onExport(id: string): void
  /** 打开设置面板回调 */
  onOpenSettings(): void
  /** 当前主题外观模式 */
  theme: 'light' | 'dark'
  /** 切换深浅色主题外观回调 */
  onToggleTheme(): void
}

/**
 * 将平铺的会话列表按更新时间分组（今天、昨天、最近 7 天、更早）。
 *
 * @param sessions - 平铺会话数组
 * @returns 过滤掉空组后的时间分组列表
 */
function groupSessions(sessions: SessionItem[]) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = 24 * 60 * 60 * 1000
  const groups: Array<{ label: string; items: SessionItem[] }> = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '最近 7 天', items: [] },
    { label: '更早', items: [] }
  ]
  for (const item of sessions) {
    const timestamp = new Date(item.updated).getTime()
    const group = !Number.isFinite(timestamp) || timestamp < today - 6 * day
      ? 3
      : timestamp >= today
        ? 0
        : timestamp >= today - day
          ? 1
          : 2
    groups[group].items.push(item)
  }
  return groups.filter(group => group.items.length > 0)
}

/**
 * 历史会话与项目侧边栏组件。
 *
 * 第一步：渲染“新建任务”快捷操作按钮。
 * 第二步：渲染项目列表与目录选择入口，展示关联的本地工作区目录。
 * 第三步：渲染会话快速搜索框。
 * 第四步：按时间周期智能分组展示历史会话列表，提供重命名、导出与删除操作。
 * 第五步：底部呈现设置入口与深浅色主题切换开关。
 */
export function SessionSidebar({
  sessions, search, searchInputRef, onSearch, currentId, projectId, projects,
  busy, connected, onChooseWorkspace, onSelectProject,
  onResume, onClear, onRename, onDelete, onExport, onOpenSettings, theme, onToggleTheme
}: SidebarProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [menuId, setMenuId] = useState<string | null>(null)
  const selectedProject = projects.find(project => project.id === projectId)

  /** 提交会话重命名更改 */
  function submitRename(id: string) {
    if (renameTitle.trim() && renameTitle.trim() !== sessions.find(item => item.id === id)?.title) {
      onRename(id, renameTitle.trim())
    }
    setRenamingId(null)
  }

  /** 弹出二次确认弹窗并执行删除 */
  function confirmDelete(item: SessionItem) {
    setMenuId(null)
    if (window.confirm(`确定删除会话“${item.title}”？此操作不可撤销。`)) {
      onDelete(item.id)
    }
  }

  return <aside className="sidebar" aria-label="项目与会话">
    {/* 第一步：新建任务按钮 */}
    <button className="new-chat" disabled={busy || !connected} onClick={onClear}>
      <MessageSquarePlus size={17} />新建任务
      <span className="shortcut">⌘ / Ctrl N</span>
    </button>

    <div className="sidebar-divider" />

    {/* 第二步：项目列表与工作区绑定 */}
    <div className="sidebar-label">
      <span>项目</span>
      <span className="label-meta">{projects.length}</span>
    </div>
    <div className="project-list" role="navigation" aria-label="项目列表">
      {projects.map(project => (
        <button
          key={project.id}
          className={project.id === projectId ? 'project-item active' : 'project-item'}
          aria-current={project.id === projectId ? 'page' : undefined}
          title={project.path || project.name}
          disabled={busy}
          onClick={() => onSelectProject(project.id)}
        >
          <Folder size={15}/>
          <span>{project.name}</span>
          {project.id === projectId && <span className="project-current"/>}
        </button>
      ))}
      {!projects.length && <p className="project-placeholder">创建项目后即可保存独立的任务和文件</p>}
    </div>

    {/* 关联物理文件夹按钮 */}
    <button
      className="workspace-item"
      title={selectedProject?.path ? `打开工作区：${selectedProject.path}` : '关联资料目录'}
      disabled={busy}
      onClick={() => selectedProject?.path ? void window.medpeer.openWorkspaceFolder(selectedProject.path) : onChooseWorkspace()}
    >
      {selectedProject?.path ? <FolderOpen size={15} /> : <Plus size={15} />}
      <span>{selectedProject?.path ? '打开工作区文件夹' : '关联本地资料文件夹'}</span>
    </button>

    <button className="project-add" disabled={busy} onClick={onChooseWorkspace}>
      <Plus size={14} /> 新建项目
    </button>

    <div className="sidebar-divider history-divider" />

    {/* 第三步：会话搜索栏 */}
    <div className="sidebar-label">
      <span>{selectedProject?.name ? `${selectedProject.name} · 任务` : '最近任务'}</span>
      <span className="label-meta">{sessions.length}</span>
    </div>
    <div className="search-wrap">
      <Search size={15} />
      <input
        ref={searchInputRef}
        className="session-search"
        aria-label="搜索会话"
        placeholder="搜索会话…"
        value={search}
        disabled={!projectId}
        onChange={event => onSearch(event.target.value)}
      />
      {search && <button type="button" aria-label="清除搜索" onClick={() => onSearch('')}><X size={14} /></button>}
    </div>

    {/* 第四步：历史会话分组列表 */}
    <nav className="session-list" aria-label="历史会话">
      {projectId && groupSessions(sessions).map(group => <section className="session-group" key={group.label}>
        <h2>{group.label}</h2>
        {group.items.map(item => <div className={`session-row ${item.id === currentId ? 'active' : ''}`} key={item.id}>
          {renamingId === item.id ? <form className="session-rename" onSubmit={event => {
            event.preventDefault()
            submitRename(item.id)
          }}>
            <input
              autoFocus
              aria-label={`会话名称 ${item.title}`}
              maxLength={160}
              value={renameTitle}
              onChange={event => setRenameTitle(event.target.value)}
              onKeyDown={event => { if (event.key === 'Escape') setRenamingId(null) }}
            />
            <button className="session-action" aria-label="保存会话名称" disabled={busy || !renameTitle.trim()}>
              <Check size={14} />
            </button>
            <button className="session-action" type="button" aria-label="取消重命名" onClick={() => setRenamingId(null)}>
              <X size={14} />
            </button>
          </form> : <>
            <button
              className="session-item"
              aria-current={item.id === currentId ? 'page' : undefined}
              title={`${item.title} · ${new Date(item.updated).toLocaleString('zh-CN')}`}
              disabled={busy}
              onClick={() => { setMenuId(null); onResume(item.id) }}
            >
              <MessageSquare size={15} />
              <span>{item.title}</span>
              {item.status && <small className="session-status">{item.status === 'question' ? '待确认' : '运行中'}</small>}
            </button>
            <div className="session-more-wrap">
              <button
                className="session-more"
                aria-label={`操作 ${item.title}`}
                aria-expanded={menuId === item.id}
                title="更多操作"
                disabled={busy}
                onClick={() => setMenuId(previous => previous === item.id ? null : item.id)}
              >
                <MoreHorizontal size={16} />
              </button>
              {menuId === item.id && <div className="session-menu">
                <button onClick={() => { setMenuId(null); setRenamingId(item.id); setRenameTitle(item.title) }}>
                  <Pencil size={14} />重命名
                </button>
                <button onClick={() => { setMenuId(null); onExport(item.id) }}>
                  <Download size={14} />导出会话
                </button>
                <button className="danger" onClick={() => confirmDelete(item)}>
                  <Trash2 size={14} />删除会话
                </button>
              </div>}
            </div>
          </>}
        </div>)}
      </section>)}

      {!projectId ? (
        <div className="sidebar-empty">
          <Folder size={20} />
          <p>选择项目后查看会话</p>
        </div>
      ) : sessions.length === 0 && (
        <div className="sidebar-empty">
          <MessageSquare size={20} />
          <p>{search ? '没有匹配的会话' : '还没有历史会话'}</p>
        </div>
      )}
    </nav>

    {/* 第五步：底部设置与外观切换 */}
    <div className="sidebar-footer">
      <button onClick={onOpenSettings}>
        <Settings2 size={17} />设置与连接
      </button>
      <button onClick={onToggleTheme}>
        {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
        {theme === 'dark' ? '浅色外观' : '深色外观'}
      </button>
    </div>
  </aside>
}
