import { useEffect, useState } from 'react'
import type { ApplicationState, ConfigScope, ConfigView } from '../../../../shared/protocol'
import { UsageSettings } from './UsageSettings'

type Tab = 'models' | 'agent' | 'permissions' | 'usage'
export type UserSettingsPageProps = {
  initialTab?: Tab
  application: ApplicationState | null
  config: ConfigView | null
  busy: boolean
  apply(method: 'model.test', params: Record<string, never>): Promise<void>
  apply(method: 'model.configure', params: { model_code: string }): Promise<void>
  apply(method: 'context.configure', params: { max_tokens: string }): Promise<void>
  apply(method: 'permission.set', params: { mode: ApplicationState['permission_mode'] }): Promise<void>
  inspect(scope: ConfigScope): Promise<void>
  save(scope: ConfigScope, kind: 'agent', content: string): Promise<void>
  remove(kind: 'agent'): Promise<void>
  testProfile(): Promise<string>
}

export function UserConfig({ initialTab = 'models', application, config, busy, apply, inspect, save, remove, testProfile }: UserSettingsPageProps) {
  const [tab, setTab] = useState<Tab>(initialTab)
  const [scope, setScope] = useState<ConfigScope>('global')
  const [model, setModel] = useState(application?.model?.model ?? '')
  const [maximum, setMaximum] = useState(String(application?.max_context_tokens ?? 475000))
  const [content, setContent] = useState('')
  const [python, setPython] = useState(application?.runtimes.python ?? '')
  const [feedback, setFeedback] = useState('')
  useEffect(() => { if (tab === 'agent') void inspect(scope) }, [tab, scope])
  useEffect(() => { setContent(config?.agent.content ?? '') }, [config])
  useEffect(() => { setTab(initialTab) }, [initialTab])
  async function run(action: () => Promise<unknown>) {
    setFeedback('')
    try { const result = await action(); setFeedback(typeof result === 'string' ? result : '已保存') }
    catch (reason) { setFeedback(String(reason)) }
  }
  const titles = { models: '模型与运行环境', agent: '用户指令', permissions: '执行权限', usage: 'Token 消耗' }
  return <div className="codex-settings modern-settings" aria-label="设置">
    <nav className="pref-sidebar" aria-label="设置分类">{Object.entries(titles).map(([id, title]) => <button key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id as Tab)}>{title}</button>)}</nav>
    <section className="pref-content"><h2>{titles[tab]}</h2>
      {tab === 'models' && <>
        <label>模型代码<input value={model} onChange={e => setModel(e.target.value)} /></label>
        <button disabled={busy} onClick={() => void run(() => apply('model.configure', { model_code: model }))}>保存模型</button>
        <button disabled={busy} onClick={() => void run(testProfile)}>测试模型</button>
        <label>上下文 Token 上限<input type="number" min="1" max="475000" value={maximum} onChange={e => setMaximum(e.target.value)} /></label>
        <button disabled={busy} onClick={() => void run(() => apply('context.configure', { max_tokens: maximum }))}>保存上限</button>
        <label>Python 路径<input value={python} onChange={e => setPython(e.target.value)} /></label>
        <button disabled={busy} onClick={() => void run(() => window.medpeer.command({ id: crypto.randomUUID(), method: 'runtime.configure', params: { python, node: application?.runtimes.node ?? '' } }))}>保存解释器</button>
        <p>脚本使用当前用户权限执行。网络搜索使用环境变量 EXA_API_KEY。</p>
        <button onClick={() => void run(() => window.medpeer.openDataDirectory('data'))}>打开数据目录</button>
        <button onClick={() => void run(() => window.medpeer.openDataDirectory('logs'))}>打开日志目录</button>
      </>}
      {tab === 'agent' && <>
        <label>指令范围<select value={scope} onChange={e => setScope(e.target.value as ConfigScope)}><option value="global">全局</option><option value="project">当前项目</option></select></label>
        <textarea aria-label="用户指令" rows={20} value={content} onChange={e => setContent(e.target.value)} />
        <button disabled={busy} onClick={() => void run(() => save(scope, 'agent', content))}>保存指令</button>
        <button disabled={busy || scope !== 'project' || !config?.overrides.agent} onClick={() => void run(() => remove('agent'))}>移除项目覆盖</button>
      </>}
      {tab === 'permissions' && <label>工具权限<select disabled={busy} value={application?.permission_mode ?? 'ask'} onChange={e => void run(() => apply('permission.set', { mode: e.target.value as ApplicationState['permission_mode'] }))}><option value="read_only">只读</option><option value="ask">操作前询问</option><option value="full">完全访问</option></select></label>}
      {tab === 'usage' && <UsageSettings />}
      {feedback && <p role="status">{feedback}</p>}
    </section>
  </div>
}
