import { useEffect, useState } from 'react'
import type { ApplicationState, ConfigScope, ConfigView } from '../../../../shared/protocol'
import { HarnessSettings } from './HarnessSettings'
import { UsageSettings } from './UsageSettings'

type Tab = 'models' | 'agent' | 'skills' | 'permissions' | 'usage'
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

export function UserConfig({ initialTab = 'models', application, busy, apply, testProfile }: UserSettingsPageProps) {
  const [tab, setTab] = useState<Tab>(initialTab)
  const [model, setModel] = useState(application?.model?.model ?? '')
  const [maximum, setMaximum] = useState(String(application?.max_context_tokens ?? 475000))
  const [python, setPython] = useState(application?.runtimes.python ?? '')
  const [feedback, setFeedback] = useState('')
  useEffect(() => { setTab(initialTab) }, [initialTab])
  async function run(action: () => Promise<unknown>) {
    setFeedback('')
    try { const result = await action(); setFeedback(typeof result === 'string' ? result : '已保存') }
    catch (reason) { setFeedback(String(reason)) }
  }
  const titles = { models: '模型与运行环境', agent: '用户指令', skills: '技能', permissions: '执行权限', usage: 'Token 消耗' }
  const descriptions = {
    models: '配置当前使用的模型、上下文容量和本机运行环境。',
    agent: '管理桌面端提供给 Agent 的补充指令。',
    skills: '分别管理系统与各项目目录下的 Agent 技能。',
    permissions: '控制 Agent 调用工具和执行操作时需要的授权。',
    usage: '查看本机各项目的 Token 使用情况。'
  }
  const navigation = [
    { id: 'models', title: '模型与运行环境', group: '通用' },
    { id: 'agent', title: '用户指令', group: '通用' },
    { id: 'skills', title: '技能', group: '通用' },
    { id: 'permissions', title: '执行权限', group: '安全' },
    { id: 'usage', title: 'Token 消耗', group: '数据' }
  ] as const
  return <div className="codex-settings modern-settings" aria-label="设置">
    <nav className="settings-navigation" aria-label="设置分类">
      <div className="settings-nav-header">
        <div className="settings-nav-title">设置</div>
        <div className="settings-nav-caption">管理模型、权限与使用情况</div>
      </div>
      <div className="settings-nav-scroll">
        {['通用', '安全', '数据'].map(group => <div className="settings-nav-group" key={group}>
          <span className="settings-nav-label">{group}</span>
          {navigation.filter(item => item.group === group).map(item => <button
            className={`settings-nav-item${tab === item.id ? ' active' : ''}`}
            key={item.id}
            aria-current={tab === item.id ? 'page' : undefined}
            onClick={() => setTab(item.id)}
          >{item.title}<span className="settings-nav-arrow" aria-hidden="true">›</span></button>)}
        </div>)}
      </div>
    </nav>
    <section className="settings-content-scroll">
      <div className="settings-content-inner">
        <header className="settings-main-header">
          <span className="settings-kicker">PREFERENCES</span>
          <h1>{titles[tab]}</h1>
          <p>{descriptions[tab]}</p>
        </header>
      {tab === 'models' && <>
        <section className="pref-section">
          <h2>模型</h2>
          <p className="pref-desc">选择并验证当前使用的模型。</p>
          <div className="pref-form"><label>模型代码<input value={model} onChange={e => setModel(e.target.value)} /></label></div>
          <div className="pref-actions">
            <button className="pref-primary" disabled={busy} onClick={() => void run(() => apply('model.configure', { model_code: model }))}>保存模型</button>
            <button disabled={busy} onClick={() => void run(testProfile)}>测试模型</button>
          </div>
        </section>
        <section className="pref-section">
          <h2>上下文</h2>
          <p className="pref-desc">设置模型每次请求可使用的最大上下文 Token 数。</p>
          <div className="pref-form"><label>上下文 Token 上限<input type="number" min="1" max="475000" value={maximum} onChange={e => setMaximum(e.target.value)} /></label></div>
          <div className="pref-actions"><button className="pref-primary" disabled={busy} onClick={() => void run(() => apply('context.configure', { max_tokens: maximum }))}>保存上限</button></div>
        </section>
        <section className="pref-section">
          <h2>运行环境</h2>
          <p className="pref-desc">脚本使用当前用户权限执行。网络搜索使用环境变量 EXA_API_KEY。</p>
          <div className="pref-form"><label>Python 路径<input value={python} onChange={e => setPython(e.target.value)} /></label></div>
          <div className="pref-actions">
            <button className="pref-primary" disabled={busy} onClick={() => void run(() => window.medpeer.command({ id: crypto.randomUUID(), method: 'runtime.configure', params: { python, node: application?.runtimes.node ?? '' } }))}>保存解释器</button>
            <button onClick={() => void run(() => window.medpeer.openDataDirectory('data'))}>打开数据目录</button>
            <button onClick={() => void run(() => window.medpeer.openDataDirectory('logs'))}>打开日志目录</button>
          </div>
        </section>
      </>}
      {/* 用户指令配置标签页 */}
      {tab === 'agent' && <HarnessSettings kind="agent" application={application} busy={busy} />}
      {/* 扩展技能管理标签页 */}
      {tab === 'skills' && <HarnessSettings kind="skills" application={application} busy={busy} />}
      {tab === 'permissions' && <section className="pref-section">
        <h2>工具权限</h2>
        <p className="pref-desc">选择 Agent 执行工具操作时使用的授权级别。</p>
        <div className="pref-setting-row"><div><strong>授权模式</strong><small>权限变更会立即应用。</small></div><select disabled={busy} value={application?.permission_mode ?? 'ask'} onChange={e => void run(() => apply('permission.set', { mode: e.target.value as ApplicationState['permission_mode'] }))}><option value="read_only">只读</option><option value="ask">操作前询问</option><option value="full">完全访问</option></select></div>
      </section>}
      {tab === 'usage' && <UsageSettings />}
      {feedback && <p className="settings-feedback" role="status">{feedback}</p>}
      </div>
    </section>
  </div>
}
