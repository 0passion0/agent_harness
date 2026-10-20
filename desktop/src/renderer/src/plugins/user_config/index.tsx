/**
 * @file index.tsx
 * @description 全局与项目偏好设置总控组件。
 * 覆盖：全局模型代码与最大上下文 Token 预算配置、
 * 插件开关、工具执行权限（只读/询问/完全）、脚本解释器路径、
 * 本机数据与日志目录、Agent 自定义指令与 Skills 目录导入编辑、以及 MCP 外部服务。
 * 界面采用现代极简美学设计，层次分明，操作丝滑。
 * @author tongliga
 * @date 2026-10-19
 */

import React, { useEffect, useState } from 'react'
import {
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  Database,
  FolderOpen,
  Layers3,
  PlugZap,
  ShieldCheck,
  UserRound,
  Wallet,
  Sparkles,
  Info,
  Sliders,
  Settings2,
  Terminal,
  FileCode,
  HardDrive,
  RefreshCw,
  ExternalLink
} from 'lucide-react'
import type { ApplicationState, ConfigScope, ConfigView, EnvironmentInfo } from '../../../../shared/protocol'
import { UsageSettings } from './UsageSettings'

/** 设置面板分类标签页类型 */
type Tab = 'models' | 'agent' | 'mcp' | 'plugins' | 'profile' | 'credits' | 'usage'

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
  save(scope: ConfigScope, kind: 'agent' | 'mcp', content: string): Promise<void>
  remove(kind: 'agent' | 'mcp'): Promise<void>
  toggleSkill(scope: ConfigScope, id: string, enabled: boolean): Promise<void>
  testMcp(scope: ConfigScope, id: string): Promise<void>
  importSkills(scope: ConfigScope): Promise<void>
  testProfile(): Promise<string>
  readSkill(scope: ConfigScope, id: string): Promise<{ content: string; source: string } | null>
  saveSkill(scope: ConfigScope, id: string, content: string): Promise<void>
}

/** 设置导航菜单分组配置 */
const categories = [
  {
    label: '基础设置',
    entries: [
      { id: 'models', label: '全局模型', icon: BrainCircuit },
      { id: 'plugins', label: '插件与权限', icon: ShieldCheck }
    ]
  },
  {
    label: '扩展能力',
    entries: [
      { id: 'agent', label: '指令与 Skills', icon: Layers3 },
      { id: 'mcp', label: 'MCP 服务', icon: PlugZap }
    ]
  },
  {
    label: '账户与用量',
    entries: [
      { id: 'profile', label: '个人信息', icon: UserRound },
      { id: 'credits', label: '研值', icon: Wallet },
      { id: 'usage', label: 'Token 消耗', icon: Database }
    ]
  }
] as const

/**
 * 用户配置中心主视图组件。
 *
 * 第一步：根据 initialTab 初始化激活页签，同步外部传入的模型代码、Token 上限与解释器路径。
 * 第二步：若处于指令 (agent) 或服务 (mcp) 页签，自动拉取当前 scope 范围的配置文件内容。
 * 第三步：渲染左侧现代分类导航菜单，支持无缝流转。
 * 第四步：按所选标签页分流渲染对应的设置卡片集合。
 *
 * @param props - 用户配置页面标准属性
 */
export function UserConfig({
  initialTab = 'models', application, config, busy, apply, inspect, save,
  remove, toggleSkill, testMcp, importSkills, testProfile, readSkill, saveSkill
}: UserSettingsPageProps) {
  const [tab, setTab] = useState<Tab>(initialTab)
  const [scope, setScope] = useState<ConfigScope>('global')
  const [modelCode, setModelCode] = useState(application?.model?.model ?? '')
  const [maxTokens, setMaxTokens] = useState(String(application?.max_context_tokens ?? 475000))
  const [pythonPath, setPythonPath] = useState('')
  const [nodePath, setNodePath] = useState('')
  const [agentContent, setAgentContent] = useState('')
  const [mcpContent, setMcpContent] = useState('{"mcpServers": {}}')
  const [skill, setSkill] = useState<{ id: string; source: string; content: string } | null>(null)
  const [environment, setEnvironment] = useState<EnvironmentInfo | null>(null)
  const [feedback, setFeedback] = useState('')
  const [pluginPending, setPluginPending] = useState(false)

  // 同步外部模型的代码与最大 Token 预算
  useEffect(() => {
    if (application?.model) setModelCode(application.model.model)
    if (application?.max_context_tokens) setMaxTokens(String(application.max_context_tokens))
  }, [application?.model?.model, application?.max_context_tokens])

  // 同步脚本运行时解释器路径
  useEffect(() => {
    if (!application) return
    setPythonPath(application.runtimes.python)
    setNodePath(application.runtimes.node)
  }, [application?.runtimes])

  // 切换到指令或 MCP 标签页时，拉取对应配置
  useEffect(() => {
    if (tab === 'agent' || tab === 'mcp') void inspect(scope)
  }, [tab, scope])

  // 配置视图更新时同步填充文本域
  useEffect(() => {
    if (!config) return
    setAgentContent(config.agent.content)
    setMcpContent(JSON.stringify({ mcpServers: config.mcp.servers }, null, 2))
  }, [config])

  /** 封装异步操作与状态反馈文本设置 */
  async function run(action: () => Promise<unknown>, message: string) {
    setFeedback('')
    try {
      await action()
      setFeedback(message)
    } catch (reason) {
      setFeedback(String(reason))
    }
  }

  /** 编辑特定技能 */
  async function editSkill(id: string) {
    const item = await readSkill(scope, id)
    if (item) setSkill({ id, ...item })
  }

  /** 打开数据或日志本地持久化目录 */
  async function openDirectory(kind: 'data' | 'logs') {
    setFeedback('')
    try {
      await window.medpeer.openDataDirectory(kind)
    } catch (reason) {
      setFeedback(String(reason))
    }
  }

  /** 检查并提取环境诊断信息 */
  async function inspectEnvironment() {
    try {
      setEnvironment(await window.medpeer.command({ id: crypto.randomUUID(), method: 'app.environment', params: {} }) as EnvironmentInfo)
    } catch (reason) {
      setFeedback(String(reason))
    }
  }

  const title = ({
    models: '全局模型',
    agent: '指令与 Skills',
    mcp: 'MCP 服务',
    plugins: '插件与权限',
    profile: '个人信息',
    credits: '研值',
    usage: 'Token 消耗'
  } as const)[tab]

  return (
    <div className="codex-settings modern-settings" aria-label="MedPeer 设置">
      {/* 左侧垂直分类导航栏 */}
      <aside className="settings-navigation">
        <div className="settings-nav-header">
          <div className="settings-nav-title">设置</div>
          <div className="settings-nav-caption">工作空间偏好与配置</div>
        </div>

        <div className="settings-nav-scroll">
          {categories.map(category => (
            <div className="settings-nav-group" key={category.label}>
              <span className="settings-nav-label">{category.label}</span>
              {category.entries.map(item => {
                const Icon = item.icon
                return (
                  <button
                    key={item.id}
                    disabled={pluginPending}
                    className={tab === item.id ? 'settings-nav-item active' : 'settings-nav-item'}
                    aria-current={tab === item.id ? 'page' : undefined}
                    onClick={() => { setTab(item.id); setFeedback('') }}
                  >
                    <Icon size={16} className="nav-item-icon" />
                    <span>{item.label}</span>
                    <ChevronRight className="settings-nav-arrow" size={13} />
                  </button>
                )
              })}
            </div>
          ))}
        </div>

        <div className="settings-nav-bottom">
          <div className="settings-brand-badge">
            <span className="settings-nav-initial">M</span>
            <div className="settings-brand-info">
              <strong>MedPeer Desktop</strong>
              <small>Local research workspace</small>
            </div>
          </div>
        </div>
      </aside>

      {/* 右侧设置表单内容滚动区 */}
      <div className="settings-content-scroll">
        <div className="settings-content-inner">
          <header className="settings-main-header">
            <span className="settings-kicker">PREFERENCES / {title}</span>
            <h1>{title}</h1>
            <p>
              {tab === 'models' ? '模型连接由当前 Python 后端提供，模型标识设置对所有项目生效。'
                : tab === 'plugins' ? '管理已发现插件的启用状态、工作流与执行权限。'
                : tab === 'agent' ? '让 Agent 理解你的项目规则，按需加载技能。'
                : tab === 'profile' ? '查看当前登录的 MedPeer 账户信息和研值余额。'
                : tab === 'usage' ? '查看各模型已报告的 Token 消耗。'
                : tab === 'credits' ? '查看账户研值余额与本机用量的研值估算。'
                : '连接可选的外部工具服务。'}
            </p>
          </header>

          {feedback && (
            <div className="settings-feedback modern-feedback" role="status">
              <Info size={15} />
              <span>{feedback}</span>
            </div>
          )}

          {/* 账户个人信息页 */}
          {tab === 'profile' && (
            <section className="pref-section usage-profile modern-card">
              <h2>MedPeer 账户</h2>
              {application?.user ? (
                <>
                  <div className="usage-profile-head">
                    {application.user.userAvatar ? (
                      <img src={application.user.userAvatar} alt="" />
                    ) : (
                      <span>{(application.user.realName || application.user.userName || '用').slice(0, 1)}</span>
                    )}
                    <div>
                      <strong>{application.user.realName || application.user.userName || '用户'}</strong>
                      <small>{application.user.userName}</small>
                    </div>
                  </div>
                  <dl className="profile-details-dl">
                    <div><dt>账号 ID</dt><dd>{application.user.userID}</dd></div>
                    <div><dt>手机号</dt><dd>{application.user.userPhone || '未提供'}</dd></div>
                    <div><dt>研值余额</dt><dd className="credits-val">{application.user.userCredit}</dd></div>
                  </dl>
                  <p className="pref-desc">账户余额由 MedPeer 提供。下方用量页的研值仅为估算，不会扣减此余额。</p>
                </>
              ) : (
                <p className="pref-desc">正在连接账户…</p>
              )}
            </section>
          )}

          {/* 研值统计 */}
          {tab === 'credits' && <UsageSettings mode="credits" user={application?.user ?? null} />}

          {/* Token 消耗报表 */}
          {tab === 'usage' && <UsageSettings mode="tokens" user={application?.user ?? null} />}

          {/* 全局模型与 Token 上限设置 */}
          {tab === 'models' && (
            <>
              {/* 模型配置卡片 */}
              <section className="pref-section modern-card">
                <div className="pref-section-heading">
                  <div>
                    <h2>模型配置</h2>
                    <p>使用当前 Python 后端配置的模型连接</p>
                  </div>
                  <span className="pref-state ok">
                    <CheckCircle2 size={14} /> {application?.model ? '已选择' : '未选择'}
                  </span>
                </div>
                <div className="pref-form">
                  <label>
                    模型 Code
                    <input
                      value={modelCode}
                      disabled={busy || !application?.model}
                      placeholder="模型标识"
                      onChange={e => setModelCode(e.target.value)}
                    />
                  </label>
                </div>
                <div className="pref-actions">
                  <button
                    className="pref-primary"
                    disabled={busy || !modelCode.trim() || modelCode === application?.model?.model}
                    onClick={() => void run(() => apply('model.configure', { model_code: modelCode }), '模型已保存，下一次任务生效')}
                  >
                    保存模型
                  </button>
                  <button
                    disabled={busy || !application?.model || modelCode !== application.model.model}
                    onClick={() => void run(() => testProfile(), '模型连接测试成功')}
                  >
                    测试连接
                  </button>
                </div>
                <p className="pref-hint">修改模型标识后请保存，再测试连接。</p>
                <div className="pref-hint modern-hint-box">
                  <CheckCircle2 size={15} />
                  <span>接口地址与凭据由 Python 后端配置，前端仅保存模型标识。</span>
                </div>
              </section>

              {/* 上下文窗口与最大 Token 预算 */}
              <section className="pref-section modern-card">
                <div className="pref-section-heading">
                  <div>
                    <h2>上下文窗口与最大 Token</h2>
                    <p>设置会话上下文背景信息窗口的最大 Token 限制</p>
                  </div>
                  <span className="pref-state ok">
                    <CheckCircle2 size={14} /> {application?.max_context_tokens ? `${application.max_context_tokens.toLocaleString()} Token` : '默认 475,000 Token'}
                  </span>
                </div>
                <div className="pref-form">
                  <label>
                    最大 Token 限制 (Tokens)
                    <input
                      type="number"
                      value={maxTokens}
                      min="1"
                      max="475000"
                      disabled={busy}
                      placeholder="475000"
                      onChange={e => setMaxTokens(e.target.value)}
                    />
                  </label>
                </div>
                <div className="pref-actions">
                  <button
                    className="pref-primary"
                    disabled={busy || !maxTokens.trim() || Number(maxTokens) < 1 || Number(maxTokens) > 475000 || Number(maxTokens) === application?.max_context_tokens}
                    onClick={() => void run(() => apply('context.configure', { max_tokens: maxTokens.trim() }), '最大 Token 预算已保存')}
                  >
                    保存 Token 限制
                  </button>
                  <button disabled={busy} onClick={() => setMaxTokens('475000')}>设为默认 475k</button>
                </div>
                <p className="pref-hint">用于控制输入提示词、技能、MCP、记忆和历史对话的上下文窗口预算。在有效窗口的 90% 自动压缩历史；模型容量较小时使用模型窗口。</p>
              </section>

              {/* 统一配置原则 */}
              <section className="pref-section modern-card">
                <h2>统一配置原则</h2>
                <div className="pref-feature-line">
                  <CheckCircle2 size={17} />
                  <div><strong>全局唯一模型</strong><small>不再创建、切换或维护多套模型资料。</small></div>
                </div>
                <div className="pref-feature-line">
                  <CheckCircle2 size={17} />
                  <div><strong>能力按需启用</strong><small>插件首次使用需确认，授权后会记住当前账号的选择。</small></div>
                </div>
              </section>
            </>
          )}

          {/* 插件与执行权限 */}
          {tab === 'plugins' && (
            <>
              {/* 插件列表 */}
              <section className="pref-section modern-card">
                <div className="pref-section-heading">
                  <div>
                    <h2>插件</h2>
                    <p>已安装与发现的扩展研究插件列表</p>
                  </div>
                </div>
                {application?.plugins.map(plugin => (
                  <div className="pref-setting-row modern-plugin-row" key={plugin.id}>
                    <div className="plugin-info-col">
                      <div className="plugin-info-title">
                        <strong>{plugin.name}</strong>
                        <span className={`plugin-tag ${plugin.enabled ? 'is-enabled' : 'is-disabled'}`}>
                          {plugin.enabled ? '已启用' : '已停用'}
                        </span>
                      </div>
                      <small className="plugin-meta-line">
                        {plugin.description} · v{plugin.version} · {plugin.enabled ? '已启用' : '已停用'} · {plugin.authorized ? '已授权' : '首次使用时确认'}
                      </small>
                      {plugin.workflows.map(workflow => (
                        <p className="pref-desc plugin-wf-item" key={workflow.name}>
                          <strong>{workflow.name}</strong> · {workflow.description}
                        </p>
                      ))}
                    </div>
                    <label className="pref-check modern-switch-label" title={`启用 ${plugin.name}`}>
                      <input
                        type="checkbox"
                        aria-label={`启用 ${plugin.name}`}
                        checked={plugin.enabled}
                        disabled={busy || pluginPending}
                        onChange={e => {
                          const enabled = e.target.checked
                          setPluginPending(true)
                          void run(
                            () => window.medpeer.command({ id: crypto.randomUUID(), method: 'plugin.set', params: { plugin_id: plugin.id, enabled: enabled ? 'true' : 'false' } }),
                            '插件状态已更新'
                          ).then(() => setPluginPending(false))
                        }}
                      />
                      <span className="modern-switch-slider" />
                    </label>
                  </div>
                ))}
                {!application?.plugins.length && <p className="pref-desc">未发现已安装的插件。</p>}
              </section>

              {/* 执行安全权限 */}
              <section className="pref-section modern-card">
                <div className="pref-section-heading">
                  <div>
                    <h2>执行权限</h2>
                    <p>管理代码执行与文件写入的默认授权策略</p>
                  </div>
                </div>
                <div className="pref-setting-row">
                  <div>
                    <strong>文件与脚本执行</strong>
                    <small>推荐“操作前询问”，敏感操作必须经过你确认。</small>
                  </div>
                  <select
                    className="modern-select"
                    value={application?.permission_mode ?? 'ask'}
                    disabled={busy}
                    onChange={e => void apply('permission.set', { mode: e.target.value as 'read_only' | 'ask' | 'full' })}
                  >
                    <option value="read_only">只读</option>
                    <option value="ask">操作前询问（推荐）</option>
                    <option value="full">完全访问</option>
                  </select>
                </div>
              </section>

              {/* 脚本运行时解释器 */}
              <section className="pref-section modern-card">
                <div className="pref-section-heading">
                  <div>
                    <h2>脚本解释器</h2>
                    <p>自定义执行 Python 和 Node.js 代码片段时使用的本机二进制解释器</p>
                  </div>
                </div>
                <p className="pref-desc">填写解释器文件的绝对路径；留空沿用项目配置，没有项目配置时使用内置环境。保存后在下一次任务执行时生效。</p>
                <div className="pref-form">
                  <label>
                    Python 解释器
                    <input
                      value={pythonPath}
                      disabled={busy}
                      placeholder="留空使用默认 Python"
                      onChange={e => setPythonPath(e.target.value)}
                    />
                  </label>
                  <label>
                    Node.js 解释器
                    <input
                      value={nodePath}
                      disabled={busy}
                      placeholder="留空使用默认 Node.js"
                      onChange={e => setNodePath(e.target.value)}
                    />
                  </label>
                </div>
                <div className="pref-actions">
                  <button
                    className="pref-primary"
                    disabled={busy || !application}
                    onClick={() => void run(() => window.medpeer.command({ id: crypto.randomUUID(), method: 'runtime.configure', params: { python: pythonPath, node: nodePath } }), '解释器设置已保存，下次任务执行时生效')}
                  >
                    保存解释器
                  </button>
                </div>
              </section>

              {/* 本机物理存储 */}
              <section className="pref-section modern-card">
                <div className="pref-section-heading">
                  <div>
                    <h2>本机存储</h2>
                    <p>浏览会话日志、用户配置与本机执行环境诊断</p>
                  </div>
                </div>
                <div className="pref-setting-row">
                  <div>
                    <strong>用户数据目录</strong>
                    <small className="path-note">{application?.data_dir ?? '本机服务连接中'}</small>
                  </div>
                  <button className="modern-outline-btn" onClick={() => void openDirectory('data')}>
                    <FolderOpen size={14} /> 打开
                  </button>
                </div>
                <div className="pref-setting-row">
                  <div>
                    <strong>当前项目日志</strong>
                    <small>查看实际执行记录和错误信息</small>
                  </div>
                  <button className="modern-outline-btn" disabled={!application?.active_project_id} onClick={() => void openDirectory('logs')}>
                    <FolderOpen size={14} /> 打开
                  </button>
                </div>
                <div className="pref-setting-row">
                  <div>
                    <strong>环境信息</strong>
                    <small>{environment ? `${environment.python_version} · ${environment.platform}` : '查看 Python 解释器与操作系统信息'}</small>
                  </div>
                  <button className="modern-outline-btn" onClick={() => void inspectEnvironment()}>
                    <RefreshCw size={14} /> 检查
                  </button>
                </div>
              </section>
            </>
          )}

          {/* 扩展能力：Agent 指令与 MCP 服务 */}
          {(tab === 'agent' || tab === 'mcp') && (
            <>
              {/* 顶部范围切换 */}
              <div className="pref-toolbar modern-scope-toolbar">
                <span className="scope-label">配置范围</span>
                <label className="scope-select-wrap">
                  <select
                    className="modern-select scope-select"
                    value={scope}
                    onChange={e => setScope(e.target.value as ConfigScope)}
                  >
                    <option value="global">全局</option>
                    <option value="project" disabled={!application?.active_project_id}>当前项目</option>
                  </select>
                </label>
              </div>

              {tab === 'agent' ? (
                <>
                  <section className="pref-section modern-card">
                    <div className="pref-section-heading">
                      <div>
                        <h2>Agent 指令</h2>
                        <p>定义长期工作原则，项目覆盖配置可独立管理。</p>
                      </div>
                    </div>
                    <textarea
                      className="pref-editor modern-editor"
                      value={agentContent}
                      placeholder="输入对智能体的全局或项目级行为准则…"
                      onChange={e => setAgentContent(e.target.value)}
                    />
                    <div className="pref-actions">
                      <button className="pref-primary" disabled={busy} onClick={() => void save(scope, 'agent', agentContent)}>
                        保存指令
                      </button>
                      <button
                        className="modern-outline-btn"
                        disabled={busy || scope !== 'project' || !config?.overrides.agent}
                        onClick={() => void remove('agent')}
                      >
                        撤销项目覆盖
                      </button>
                    </div>
                  </section>

                  <section className="pref-section modern-card">
                    <div className="pref-section-heading">
                      <div>
                        <h2>Skills</h2>
                        <p>扩展工具库与自定义技能集合</p>
                      </div>
                      <button className="modern-outline-btn" onClick={() => void importSkills(scope)}>
                        <FolderOpen size={14} /> 导入目录
                      </button>
                    </div>
                    {config?.skills.map(item => (
                      <div className="pref-setting-row modern-skill-row" key={item.id}>
                        <div>
                          <strong>{item.id}</strong>
                          <small>{item.source}</small>
                        </div>
                        <div className="skill-actions-group">
                          <button className="modern-outline-btn-sm" onClick={() => void editSkill(item.id)}>
                            编辑
                          </button>
                          <label className="pref-check modern-switch-label" title={`启用 ${item.id}`}>
                            <input
                              type="checkbox"
                              checked={item.enabled}
                              disabled={busy}
                              onChange={e => void toggleSkill(scope, item.id, e.target.checked)}
                            />
                            <span className="modern-switch-slider" />
                          </label>
                        </div>
                      </div>
                    ))}
                    {!config?.skills.length && <p className="pref-desc">目前没有自定义 Skill。内置工具不受影响。</p>}
                    {skill && (
                      <div className="skill-editor-box">
                        <label className="pref-desc skill-editor-label">
                          <FileCode size={14} />
                          <strong>{skill.id} / SKILL.md</strong>
                        </label>
                        <textarea
                          className="pref-editor modern-editor"
                          value={skill.content}
                          onChange={e => setSkill({ ...skill, content: e.target.value })}
                        />
                        <button className="pref-primary" onClick={() => void saveSkill(scope, skill.id, skill.content)}>
                          保存 Skill
                        </button>
                      </div>
                    )}
                  </section>
                </>
              ) : (
                <section className="pref-section modern-card">
                  <div className="pref-section-heading">
                    <div>
                      <h2>MCP 连接</h2>
                      <p>仅配置当前工作区需要的外部服务。</p>
                    </div>
                  </div>
                  <textarea
                    className="pref-editor modern-editor"
                    spellCheck={false}
                    value={mcpContent}
                    onChange={e => setMcpContent(e.target.value)}
                  />
                  <div className="pref-actions">
                    <button className="pref-primary" disabled={busy} onClick={() => void save(scope, 'mcp', mcpContent)}>
                      保存配置
                    </button>
                    <button
                      className="modern-outline-btn"
                      disabled={busy || scope !== 'project' || !config?.overrides.mcp}
                      onClick={() => void remove('mcp')}
                    >
                      撤销项目覆盖
                    </button>
                  </div>
                  {Object.keys(config?.mcp.servers ?? {}).map(id => (
                    <div className="pref-setting-row" key={id}>
                      <strong>{id}</strong>
                      <button className="modern-outline-btn-sm" onClick={() => void testMcp(scope, id)}>
                        <PlugZap size={13} /> 测试连接
                      </button>
                    </div>
                  ))}
                </section>
              )}
            </>
          )}

          <footer className="settings-footer-note">
            <span>MedPeer Desktop · 本地优先架构 · 设置仅对当前设备生效</span>
          </footer>
        </div>
      </div>
    </div>
  )
}
