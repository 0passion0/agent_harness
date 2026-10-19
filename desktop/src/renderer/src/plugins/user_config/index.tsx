/**
 * @file index.tsx
 * @description 全局与项目偏好设置总控组件。
 * 覆盖：全局模型代码与最大上下文 Token 预算配置、Exa 网络搜索密钥、内置科研数据分析能力、
 * 插件开关、工具执行权限（只读/询问/完全）、脚本解释器路径、
 * 本机数据与日志目录、Agent 自定义指令与 Skills 目录导入编辑、以及 MCP 外部服务。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useState } from 'react'
import {
  BrainCircuit, CheckCircle2, ChevronRight, Code2, Database, FolderOpen,
  Globe2, Layers3, PlugZap, ShieldCheck, Sparkles, UserRound, Wallet, Wrench
} from 'lucide-react'
import type { ConfigScope, EnvironmentInfo } from '../../../../shared/protocol'
import type { UserSettingsPageProps } from '../settings'
import { PluginEditor } from './PluginEditor'
import { UsageSettings } from './UsageSettings'

/** 设置面板分类标签页类型 */
type Tab = 'models' | 'tools' | 'agent' | 'mcp' | 'plugins' | 'profile' | 'credits' | 'usage'

/** 设置导航菜单分组配置 */
const categories = [
  {
    label: '基础设置',
    entries: [
      { id: 'models', label: '全局模型', icon: BrainCircuit },
      { id: 'tools', label: '内置工具', icon: Wrench },
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
 * 第三步：渲染左侧分类导航菜单，支持页面无缝切换。
 * 第四步：按所选标签页分流渲染对应的表单控件（全局模型、上下文预算、Exa 搜索、权限模式等）。
 *
 * @param props - 用户配置页面标准属性
 */
export function UserConfig({
  initialTab = 'models', application, config, busy, apply, inspect, save,
  remove, toggleSkill, testMcp, importSkills, testProfile, readSkill, saveSkill
}: UserSettingsPageProps) {
  const [tab, setTab] = useState<Tab>(initialTab)
  const [scope, setScope] = useState<ConfigScope>('global')
  const [exaKey, setExaKey] = useState('')
  const [modelCode, setModelCode] = useState(application?.model?.model ?? '')
  const [maxTokens, setMaxTokens] = useState(String(application?.max_context_tokens ?? 128000))
  const [pythonPath, setPythonPath] = useState('')
  const [nodePath, setNodePath] = useState('')
  const [agentContent, setAgentContent] = useState('')
  const [mcpContent, setMcpContent] = useState('{"mcpServers": {}}')
  const [skill, setSkill] = useState<{ id: string; source: string; content: string } | null>(null)
  const [environment, setEnvironment] = useState<EnvironmentInfo | null>(null)
  const [feedback, setFeedback] = useState('')
  const [pluginDirty, setPluginDirty] = useState(false)
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
    tools: '内置工具',
    agent: '指令与 Skills',
    mcp: 'MCP 服务',
    plugins: '插件与权限',
    profile: '个人信息',
    credits: '研值',
    usage: 'Token 消耗'
  } as const)[tab]

  return <div className="codex-settings" aria-label="MedPeer 设置">
    {/* 左侧垂直分类导航栏 */}
    <aside className="settings-navigation">
      <div className="settings-nav-title">设置</div>
      <div className="settings-nav-caption">工作空间偏好</div>
      {categories.map(category => <div className="settings-nav-group" key={category.label}>
        <span className="settings-nav-label">{category.label}</span>
        {category.entries.map(item => {
          const Icon = item.icon
          return <button
            key={item.id}
            disabled={pluginDirty || pluginPending}
            className={tab === item.id ? 'settings-nav-item active' : 'settings-nav-item'}
            aria-current={tab === item.id ? 'page' : undefined}
            onClick={() => { setTab(item.id); setFeedback('') }}
          >
            <Icon size={16}/>{item.label}
            <ChevronRight className="settings-nav-arrow" size={13}/>
          </button>
        })}
      </div>)}
      <div className="settings-nav-bottom">
        <span className="settings-nav-initial">M</span>
        <div><strong>MedPeer</strong><small>Local research workspace</small></div>
      </div>
    </aside>

    {/* 右侧设置表单内容滚动区 */}
    <div className="settings-content-scroll">
      <div className="settings-content-inner">
        <div className="settings-main-header">
          <span className="settings-kicker">PREFERENCES / {title}</span>
          <h1>{title}</h1>
          <p>{
            tab === 'models' ? '默认使用 Agnes，可填写 MedPeer 支持的模型 Code，所有项目共用。'
            : tab === 'tools' ? '随桌面应用提供的 Agent 能力，无需逐一安装或启用。'
            : tab === 'plugins' ? '管理插件启用状态、执行权限与本机工作目录。'
            : tab === 'agent' ? '让 Agent 理解你的项目规则，按需加载技能。'
            : tab === 'profile' ? '查看当前登录的 MedPeer 账户信息和研值余额。'
            : tab === 'usage' ? '查看各模型已报告的 Token 消耗。'
            : tab === 'credits' ? '查看账户研值余额与本机用量的研值估算。'
            : '连接可选的外部工具服务。'
          }</p>
        </div>

        {feedback && <div className="settings-feedback" role="status">{feedback}</div>}

        {/* 账户个人信息页 */}
        {tab === 'profile' && <section className="pref-section usage-profile">
          <h2>MedPeer 账户</h2>
          {application?.user ? <>
            <div className="usage-profile-head">
              {application.user.userAvatar ? <img src={application.user.userAvatar} alt=""/> : <span>{(application.user.realName || application.user.userName || '用').slice(0, 1)}</span>}
              <div>
                <strong>{application.user.realName || application.user.userName || '用户'}</strong>
                <small>{application.user.userName}</small>
              </div>
            </div>
            <dl>
              <div><dt>账号 ID</dt><dd>{application.user.userID}</dd></div>
              <div><dt>手机号</dt><dd>{application.user.userPhone || '未提供'}</dd></div>
              <div><dt>研值余额</dt><dd>{application.user.userCredit}</dd></div>
            </dl>
            <p className="pref-desc">账户余额由 MedPeer 提供。下方用量页的研值仅为估算，不会扣减此余额。</p>
          </> : <p className="pref-desc">正在连接账户…</p>}
        </section>}

        {/* 研值统计 */}
        {tab === 'credits' && <UsageSettings mode="credits" user={application?.user ?? null} />}

        {/* Token 消耗报表 */}
        {tab === 'usage' && <UsageSettings mode="tokens" user={application?.user ?? null} />}

        {/* 全局模型与 Token 上限设置 */}
        {tab === 'models' && <>
          <section className="pref-section">
            <div className="pref-section-heading">
              <div>
                <h2>MedPeer 模型</h2>
                <p>所有项目和任务使用统一的 MedPeer 模型</p>
              </div>
              <span className="pref-state ok"><CheckCircle2 size={14}/> 已就绪</span>
            </div>
            <div className="pref-form">
              <label>
                模型 Code
                <input value={modelCode} disabled={busy || !application?.model} placeholder="agnes-3.0-flash" onChange={e => setModelCode(e.target.value)} />
              </label>
            </div>
            <div className="pref-actions">
              <button disabled={busy || !modelCode.trim() || modelCode === application?.model?.model} onClick={() => void run(() => apply('model.configure', { model_code: modelCode }), '模型已保存，下一次任务生效')}>保存模型</button>
              <button disabled={busy || !application?.model} onClick={() => setModelCode('agnes-3.0-flash')}>使用默认 Agnes</button>
              <button disabled={busy || !application?.model || modelCode !== application.model.model} onClick={() => void run(() => testProfile(), '模型连接测试成功')}>测试连接</button>
            </div>
            <p className="pref-hint">默认 Code 为 agnes-3.0-flash。修改后请保存，再测试连接。</p>
            <div className="pref-hint">
              <CheckCircle2 size={15}/>
              <span>模型连接由 MedPeer 账户提供，无需配置提供商、接口地址或 API Key。</span>
            </div>
          </section>

          {/* 上下文窗口与最大 Token 预算 */}
          <section className="pref-section">
            <div className="pref-section-heading">
              <div>
                <h2>上下文窗口与最大 Token</h2>
                <p>设置会话上下文背景信息窗口的最大 Token 限制</p>
              </div>
              <span className="pref-state ok">
                <CheckCircle2 size={14}/> {application?.max_context_tokens ? `${application.max_context_tokens.toLocaleString()} Token` : '默认 128,000 Token'}
              </span>
            </div>
            <div className="pref-form">
              <label>
                最大 Token 限制 (Tokens)
                <input type="number" value={maxTokens} disabled={busy} placeholder="128000" onChange={e => setMaxTokens(e.target.value)} />
              </label>
            </div>
            <div className="pref-actions">
              <button disabled={busy || !maxTokens.trim() || Number(maxTokens) === application?.max_context_tokens} onClick={() => void run(() => apply('context.configure', { max_tokens: maxTokens.trim() }), '最大 Token 预算已保存')}>保存 Token 限制</button>
              <button disabled={busy} onClick={() => setMaxTokens('128000')}>设为默认 128k</button>
              <button disabled={busy} onClick={() => setMaxTokens('475000')}>设为 475k</button>
            </div>
            <p className="pref-hint">用于控制输入提示词、技能、MCP、记忆和历史对话的上下文窗口预算。</p>
          </section>

          <section className="pref-section">
            <h2>统一配置原则</h2>
            <div className="pref-feature-line">
              <CheckCircle2 size={17}/>
              <div><strong>全局唯一模型</strong><small>不再创建、切换或维护多套模型资料。</small></div>
            </div>
            <div className="pref-feature-line">
              <CheckCircle2 size={17}/>
              <div><strong>内置能力默认可用</strong><small>Exa 搜索、表格画像、Python 分析、报告交付由 Agent 按任务选择。</small></div>
            </div>
          </section>
        </>}

        {/* 内置工具 */}
        {tab === 'tools' && <>
          <section className="pref-section">
            <div className="pref-section-heading">
              <div>
                <h2>Exa 网络搜索</h2>
                <p>已集成到 Agent 的默认工具列表</p>
              </div>
              <span className={application?.exa.configured ? 'pref-state ok' : 'pref-state'}>
                {application?.exa.configured ? '已就绪' : '等待配置状态'}
              </span>
            </div>
            <div className="pref-tool-summary">
              <div className="pref-icon blue"><Globe2 size={20}/></div>
              <div>
                <strong>联网资料检索</strong>
                <small>自动获取网页标题、来源 URL 和有限摘要。默认即可使用，也可填写自己的 API Key。</small>
              </div>
            </div>
            <div className="pref-form">
              <label>
                自定义 Exa API Key（可选）
                <input type="password" value={exaKey} autoComplete="new-password" disabled={busy} placeholder="留空使用现有配置 · 填写可替换密钥" onChange={e => setExaKey(e.target.value)} />
              </label>
            </div>
            <div className="pref-actions">
              <button className="pref-primary" disabled={busy || !exaKey.trim()} onClick={() => void run(async () => {
                await window.medpeer.command({ id: crypto.randomUUID(), method: 'exa.configure', params: { api_key: exaKey.trim() } })
                setExaKey('')
              }, 'Exa 自定义密钥已保存，搜索已就绪')}>保存自定义密钥</button>
              {application?.exa.configured && <button disabled={busy} onClick={() => void run(() => window.medpeer.command({ id: crypto.randomUUID(), method: 'exa.clear', params: {} }), 'Exa 已恢复默认密钥')}>恢复默认密钥</button>}
            </div>
          </section>

          <section className="pref-section">
            <div className="pref-section-heading">
              <div>
                <h2>数据分析与研究报告</h2>
                <p>原生包含，无需额外路由模型</p>
              </div>
              <span className="pref-state ok"><CheckCircle2 size={14}/> 默认开启</span>
            </div>
            <div className="pref-feature-line">
              <Database size={18}/>
              <div><strong>表格画像与质量检查</strong><small>CSV / Excel / Parquet 数据行数、缺失值、字段类型、分布统计。</small></div>
            </div>
            <div className="pref-feature-line">
              <Code2 size={18}/>
              <div><strong>可复现分析</strong><small>使用当前 Python 执行统计与绘图，记录脚本、执行证据与输出文件。</small></div>
            </div>
            <div className="pref-feature-line">
              <Sparkles size={18}/>
              <div><strong>科研报告交付</strong><small>基于真实计算结果生成 Markdown 报告及证据文件，可在任务右侧直接审查。</small></div>
            </div>
            <div className="pref-hint">
              <ShieldCheck size={15}/>
              <span>内置不意味着无条件执行：写入和脚本运行仍遵守“操作前询问”权限；在线搜索需有效密钥及网络。</span>
            </div>
          </section>
        </>}

        {/* 插件与执行权限 */}
        {tab === 'plugins' && <>
          <section className="pref-section">
            <h2>插件</h2>
            {application?.plugins.map(plugin => <div className="pref-setting-row" key={plugin.id}>
              <div>
                <strong>{plugin.name}</strong>
                <small>{plugin.description} · {plugin.loaded ? '已加载' : plugin.enabled ? '已启用' : '已停用'}</small>
              </div>
              <label className="pref-check">
                <input
                  type="checkbox"
                  aria-label={`启用 ${plugin.name}`}
                  checked={plugin.enabled}
                  disabled={busy || pluginPending || pluginDirty || !['data_analysis', 'exa'].includes(plugin.id)}
                  onChange={e => {
                    const enabled = e.target.checked
                    setPluginPending(true)
                    void run(
                      () => window.medpeer.command({ id: crypto.randomUUID(), method: 'plugin.set', params: { plugin_id: plugin.id, enabled: enabled ? 'true' : 'false' } }),
                      '插件状态已更新'
                    ).then(() => setPluginPending(false))
                  }}
                />
              </label>
            </div>)}
          </section>

          {application?.build_mode === 'development' && (
            <PluginEditor busy={busy || pluginPending} run={run} onDirty={setPluginDirty}/>
          )}

          {/* 执行安全权限 */}
          <section className="pref-section">
            <h2>执行权限</h2>
            <div className="pref-setting-row">
              <div>
                <strong>文件与脚本执行</strong>
                <small>推荐“操作前询问”，敏感操作必须经过你确认。</small>
              </div>
              <select value={application?.permission_mode ?? 'ask'} disabled={busy} onChange={e => void apply('permission.set', { mode: e.target.value as 'read_only' | 'ask' | 'full' })}>
                <option value="read_only">只读</option>
                <option value="ask">操作前询问（推荐）</option>
                <option value="full">完全访问</option>
              </select>
            </div>
          </section>

          {/* 脚本运行时解释器 */}
          <section className="pref-section">
            <h2>脚本解释器</h2>
            <p className="pref-desc">填写解释器文件的绝对路径；留空沿用项目配置，没有项目配置时使用内置环境。保存后在下一次任务执行时生效。</p>
            <div className="pref-form">
              <label>Python 解释器<input value={pythonPath} disabled={busy} placeholder="留空使用默认 Python" onChange={e => setPythonPath(e.target.value)}/></label>
              <label>Node.js 解释器<input value={nodePath} disabled={busy} placeholder="留空使用默认 Node.js" onChange={e => setNodePath(e.target.value)}/></label>
            </div>
            <div className="pref-actions">
              <button className="pref-primary" disabled={busy || !application} onClick={() => void run(() => window.medpeer.command({ id: crypto.randomUUID(), method: 'runtime.configure', params: { python: pythonPath, node: nodePath } }), '解释器设置已保存，下次任务执行时生效')}>保存解释器</button>
            </div>
          </section>

          {/* 本机物理存储 */}
          <section className="pref-section">
            <h2>本机存储</h2>
            <div className="pref-setting-row">
              <div><strong>用户数据目录</strong><small className="path-note">{application?.data_dir ?? '本机服务连接中'}</small></div>
              <button onClick={() => void openDirectory('data')}>打开 <FolderOpen size={14}/></button>
            </div>
            <div className="pref-setting-row">
              <div><strong>当前项目日志</strong><small>查看实际执行记录和错误信息</small></div>
              <button disabled={!application?.active_project_id} onClick={() => void openDirectory('logs')}>打开 <FolderOpen size={14}/></button>
            </div>
            <div className="pref-setting-row">
              <div><strong>环境信息</strong><small>{environment ? `${environment.python_version} · ${environment.platform}` : '查看 Python 解释器与操作系统信息'}</small></div>
              <button onClick={() => void inspectEnvironment()}>检查</button>
            </div>
          </section>
        </>}

        {/* 扩展能力：Agent 指令与 MCP 服务 */}
        {(tab === 'agent' || tab === 'mcp') && <>
          <div className="pref-toolbar">
            <label>
              配置范围{' '}
              <select value={scope} onChange={e => setScope(e.target.value as ConfigScope)}>
                <option value="global">全局</option>
                <option value="project" disabled={!application?.active_project_id}>当前项目</option>
              </select>
            </label>
          </div>

          {tab === 'agent' ? <>
            <section className="pref-section">
              <h2>Agent 指令</h2>
              <p className="pref-desc">定义长期工作原则，项目覆盖配置可独立管理。</p>
              <textarea className="pref-editor" value={agentContent} onChange={e => setAgentContent(e.target.value)} />
              <div className="pref-actions">
                <button className="pref-primary" disabled={busy} onClick={() => void save(scope, 'agent', agentContent)}>保存指令</button>
                <button disabled={busy || scope !== 'project' || !config?.overrides.agent} onClick={() => void remove('agent')}>撤销项目覆盖</button>
              </div>
            </section>
            <section className="pref-section">
              <div className="pref-section-heading">
                <h2>Skills</h2>
                <button onClick={() => void importSkills(scope)}>导入目录</button>
              </div>
              {config?.skills.map(item => <div className="pref-setting-row" key={item.id}>
                <div><strong>{item.id}</strong><small>{item.source}</small></div>
                <button onClick={() => void editSkill(item.id)}>编辑</button>
                <label className="pref-check"><input type="checkbox" checked={item.enabled} disabled={busy} onChange={e => void toggleSkill(scope, item.id, e.target.checked)}/></label>
              </div>)}
              {!config?.skills.length && <p className="pref-desc">目前没有自定义 Skill。内置工具不受影响。</p>}
              {skill && <>
                <label className="pref-desc">{skill.id} / SKILL.md</label>
                <textarea className="pref-editor" value={skill.content} onChange={e => setSkill({ ...skill, content: e.target.value })}/>
                <button className="pref-primary" onClick={() => void saveSkill(scope, skill.id, skill.content)}>保存 Skill</button>
              </>}
            </section>
          </> : (
            <section className="pref-section">
              <h2>MCP 连接</h2>
              <p className="pref-desc">仅用于外部服务。Exa / 数据分析 / 报告工具已经内置，无需额外配置 MCP。</p>
              <textarea className="pref-editor" spellCheck={false} value={mcpContent} onChange={e => setMcpContent(e.target.value)}/>
              <div className="pref-actions">
                <button className="pref-primary" disabled={busy} onClick={() => void save(scope, 'mcp', mcpContent)}>保存配置</button>
                <button disabled={busy || scope !== 'project' || !config?.overrides.mcp} onClick={() => void remove('mcp')}>撤销项目覆盖</button>
              </div>
              {Object.keys(config?.mcp.servers ?? {}).map(id => <div className="pref-setting-row" key={id}>
                <strong>{id}</strong>
                <button onClick={() => void testMcp(scope, id)}>测试连接</button>
              </div>)}
            </section>
          )}
        </>}

        <div className="settings-footer-note">MedPeer Desktop · 本地优先 · 设置仅对当前设备生效</div>
      </div>
    </div>
  </div>
}
