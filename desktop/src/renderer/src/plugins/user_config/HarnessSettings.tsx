/**
 * @file HarnessSettings.tsx
 * @description Harness 指令与技能资源桌面端设置面板组件。
 * 提供系统全局 (~/.agent-harness) 与特定项目 (<workspace>/.agents) 的 AGENTS.md 指令编辑、
 * 外部 ZIP 技能归档安全导入、已有技能列表浏览与 SKILL.md 在线修改能力。
 * @author tongliga
 * @date 2026-10-22
 */

import { useEffect, useRef, useState } from 'react'
import type { ApplicationState, ConfigScope, HarnessConfig, HarnessProject } from '../../../../shared/protocol'

/**
 * 资源类型分类：'agent' 表示全局/项目指令文件，'skills' 表示扩展技能集合。
 */
type Kind = 'agent' | 'skills'

/**
 * 格式化输出项目选择下拉菜单项的展示标签。
 *
 * @param project 项目摘要信息对象
 * @returns 格式化后的 `名称 — 路径` 显示文本
 * @author tongliga
 * @date 2026-10-22
 */
export function harnessProjectLabel(project: HarnessProject): string {
  return `${project.name} — ${project.path}`
}

/**
 * HarnessSettings 设置面板组件属性契约。
 */
export type HarnessSettingsProps = {
  /** 当前选中的配置类型（指令或技能） */
  kind: Kind
  /** 桌面端全局应用状态快照 */
  application: ApplicationState | null
  /** 系统是否处于正在执行或忙碌状态 */
  busy: boolean
}

/**
 * Harness 资源配置主视图组件。
 *
 * @param props 组件入参对象，包含资源类型、应用状态快照与忙碌标识。
 * @returns React 虚拟 DOM 节点。
 * @author tongliga
 * @date 2026-10-22
 */
export function HarnessSettings({ kind, application, busy }: HarnessSettingsProps) {
  // 基础状态：选中的项目 ID、可用项目列表及加载状态
  const [projectId, setProjectId] = useState('')
  const [projects, setProjects] = useState<HarnessProject[]>([])
  const [projectsLoading, setProjectsLoading] = useState(true)

  // 资源状态：当前作用域 Harness 配置快照、编辑缓冲区内容与正在编辑的技能标识
  const [config, setConfig] = useState<HarnessConfig | null>(null)
  const [content, setContent] = useState('')
  const [editing, setEditing] = useState<string | null>(null)

  // 操作状态：提示反馈信息、项目加载错误、保存提交中与数据加载中
  const [feedback, setFeedback] = useState('')
  const [projectsError, setProjectsError] = useState('')
  const [pending, setPending] = useState(false)
  const [loading, setLoading] = useState(false)

  // 请求版本计数器，避免网络并发竞态时陈旧响应覆写最新状态
  const request = useRef(0)
  const scope: ConfigScope = projectId ? 'project' : 'global'
  const locked = busy || pending || loading || projectsLoading
  const applicationProjectsKey = JSON.stringify(application?.projects.map(({ id, path }) => [id, path])) ?? '[]'

  // 第一步：监听应用项目列表变更，拉取可用项目列表
  useEffect(() => {
    let current = true
    setProjectsLoading(true)
    setProjectsError('')
    void window.medpeer.command({ id: crypto.randomUUID(), method: 'harness.projects', params: {} })
      .then(value => { if (current) setProjects(value as HarnessProject[]) })
      .catch(reason => { if (current) setProjectsError(String(reason)) })
      .finally(() => { if (current) setProjectsLoading(false) })
    return () => { current = false }
  }, [applicationProjectsKey])

  // 第二步：项目列表变动时断言当前选中的项目是否依然有效，若失效则回退至系统范围
  useEffect(() => {
    if (!projectsLoading && projectId && !projects.some(project => project.id === projectId)) setProjectId('')
  }, [projectId, projects, projectsLoading])

  // 第三步：根据当前作用域与项目 ID 请求后端巡检配置并更新界面
  useEffect(() => {
    const current = ++request.current
    setConfig(null)
    setEditing(null)
    setFeedback('')
    if (projectsLoading) {
      setLoading(false)
      return () => { if (request.current === current) request.current++ }
    }
    setLoading(true)
    void window.medpeer.command({ id: crypto.randomUUID(), method: 'harness.inspect', params: { scope, project_id: projectId } })
      .then(value => { if (request.current === current) setConfig(value as HarnessConfig) })
      .catch(reason => { if (request.current === current) setFeedback(String(reason)) })
      .finally(() => { if (request.current === current) setLoading(false) })
    return () => { if (request.current === current) request.current++ }
  }, [scope, projectId, projectsLoading])

  // 第四步：封装通用的异步请求执行器，捕获异常并反馈处理提示
  async function run(action: () => Promise<HarnessConfig | null>, onSuccess?: () => void) {
    const current = request.current
    setPending(true)
    setFeedback('')
    try {
      const result = await action()
      if (request.current !== current || !result) return
      setConfig(result)
      onSuccess?.()
      setFeedback('已保存')
    } catch (reason) {
      setFeedback(String(reason))
    } finally {
      setPending(false)
    }
  }

  // 第五步：根据指令或技能类型分别向后端发起保存或删除变更请求
  async function mutate(method: 'harness.save' | 'harness.remove', name: string, nextContent = ''): Promise<HarnessConfig> {
    const id = crypto.randomUUID()
    if (method === 'harness.save') return await window.medpeer.command({ id, method, params: { scope, project_id: projectId, kind: kind === 'agent' ? 'agent' : 'skill', name, content: nextContent } }) as HarnessConfig
    return await window.medpeer.command({ id, method, params: { scope, project_id: projectId, kind: kind === 'agent' ? 'agent' : 'skill', name } }) as HarnessConfig
  }

  // 第六步：渲染顶部范围选择工具栏、指令编辑卡片或技能管理卡片列表
  return <section className="pref-section">
    <div className="pref-section-heading"><div><h2>{kind === 'agent' ? '指令' : '技能'}</h2><p>系统配置保存在 ~/.agent-harness；项目配置保存在所选项目目录。</p></div></div>
    <div className="pref-toolbar"><label>应用范围<select value={projectId} disabled={busy || pending || projectsLoading} onChange={e => setProjectId(e.target.value)}><option value="">系统 ~/.agent-harness</option>{projects.map(project => <option key={project.id} value={project.id}>{harnessProjectLabel(project)}</option>)}</select></label></div>
    {loading && <p className="pref-desc">正在读取…</p>}
    {projectsError && <p className="settings-feedback" role="status">{projectsError}</p>}
    {kind === 'agent' && <>
      <p className="pref-desc">编辑 Agent 指令文件的内容。</p>
      <textarea className="pref-editor" aria-label="Agent 指令" rows={20} value={config?.agent.content ?? ''} onChange={e => setConfig(config ? { ...config, agent: { ...config.agent, content: e.target.value } } : config)} disabled={!config || locked} />
      <div className="pref-actions">
        <button className="pref-primary" disabled={!config || locked} onClick={() => void run(() => mutate('harness.save', '', config?.agent.content ?? ''))}>保存指令</button>
        <button disabled={!config?.agent.exists || locked} onClick={() => void run(() => mutate('harness.remove', ''))}>删除指令文件</button>
        {config?.agent.source && <small>{config.agent.source}</small>}
      </div>
    </>}
    {kind === 'skills' && <>
      <div className="pref-actions">
        <button disabled={locked} onClick={() => void run(() => window.medpeer.importSkills(scope, projectId))}>导入 ZIP 技能</button>
      </div>
      <p className="pref-desc">ZIP 包含一个技能，可直接放置 SKILL.md 或使用一层技能目录。导入保留附带文件，删除会移除整个技能目录。</p>
      {config && <p className="pref-desc">技能目录：{config.root}/skills</p>}
      {config?.skills.length === 0 && <p className="pref-desc">此范围还没有技能。</p>}
      {config?.skills.map(skill => <div className="skill-row" key={skill.directory}>
        <div><strong>{skill.name}</strong><small>{skill.description || skill.directory}</small></div>
        <div className="pref-actions">
          <button disabled={locked} onClick={() => { setEditing(editing === skill.name ? null : skill.name); setContent(skill.content) }}>{editing === skill.name ? '取消编辑' : '编辑'}</button>
          <button disabled={locked} onClick={() => void run(() => mutate('harness.remove', skill.name), () => setEditing(null))}>删除</button>
        </div>
        {editing === skill.name && <div className="skill-editor-box">
          <label className="skill-editor-label" htmlFor={`skill-${skill.name}`}>SKILL.md 内容</label>
          <textarea id={`skill-${skill.name}`} className="pref-editor" rows={12} value={content} onChange={e => setContent(e.target.value)} disabled={locked} />
          <div className="pref-actions"><button className="pref-primary" disabled={locked} onClick={() => void run(() => mutate('harness.save', skill.name, content), () => setEditing(null))}>保存技能</button></div>
        </div>}
      </div>)}
    </>}
    {feedback && <p className="settings-feedback" role="status">{feedback}</p>}
  </section>
}
