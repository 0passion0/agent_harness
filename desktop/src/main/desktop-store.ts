/**
 * @file desktop-store.ts
 * @description 账号与项目级本地数据管理存储引擎。
 * 负责本地偏好设置（模型、解释器、权限、插件）、Agent 规则配置（agent.md、mcp.json）、
 * 技能目录导入导出、附件与生成交付物管理，以及 Token/研值用量统计的聚合计算。
 * @author tongliga
 * @date 2026-10-16
 */

import { createHash, randomUUID } from 'node:crypto'
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, statSync, unlinkSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { ArtifactRef, AttachmentRef, ConfigScope, ConfigView, Json, UsageStats } from '../shared/protocol'
import { writeAtomic } from './local-store'

/**
 * 桌面客户端通用偏好配置契约。
 */
type DesktopSettings = {
  /** 默认调用的模型代码标识 */
  model_code: string
  /** 自定义外部脚本解释器物理路径 */
  runtimes: { python: string; node: string }
  /** 工具调用权限控制模式 */
  permission_mode: 'read_only' | 'ask' | 'full'
  /** 允许的最大上下文 Token 窗口上限 */
  max_context_tokens: number
  /** 各插件的启用/禁用状态映射字典 */
  plugins: Record<string, boolean>
}

/**
 * 单次模型调用记录契约。
 */
type UsageRecord = {
  /** 关联的消息请求唯一 ID */
  request_id: string
  /** ISO 格式请求发起时间戳 */
  requested_at: string
  /** 使用的模型名称 */
  model: string
  /** 请求来源（通用模型调用或免研值的 JEV 模式） */
  source: 'model' | 'jev'
  /** 实际消耗的 Token 明细 */
  usage: {
    input_tokens: number
    output_tokens: number
    total_tokens: number
    input_token_details?: { cache_read?: number }
    output_token_details?: { reasoning?: number }
  } | null
  /** 关联的计费及研值区间估算 */
  cost_estimate: {
    minimum_cny: string
    maximum_cny: string
    minimum_credits: string
    maximum_credits: string
    rate_dates: string[]
  } | null
}

/**
 * 高精度十进制字符串求和辅助函数。
 * 将带小数点的数值转换为 BigInt 进行整数加法运算，避免 IEEE-754 浮点累加误差。
 *
 * @param values - 待求和的十进制数字符串数组
 * @returns 求和后的格式化字符串
 */
const decimalSum = (values: string[]): string => {
  const places = Math.max(...values.map(value => value.split('.')[1]?.length ?? 0))
  const factor = 10n ** BigInt(places)
  const total = values.reduce((sum, value) => {
    const [whole, fraction = ''] = value.split('.')
    return sum + BigInt(`${whole}${fraction.padEnd(places, '0')}`)
  }, 0n)
  if (!places) return String(total)
  const digits = (total < 0n ? -total : total).toString().padStart(places + 1, '0')
  const fraction = digits.slice(-places).replace(/0+$/, '')
  return `${total < 0n ? '-' : ''}${digits.slice(0, -places)}${fraction ? `.${fraction}` : ''}`
}

/** 默认偏好设置常量 */
const defaults: DesktopSettings = {
  model_code: '',
  runtimes: { python: '', node: '' },
  permission_mode: 'ask',
  max_context_tokens: 475000,
  plugins: { 'web.search': true }
}

/**
 * 校验候选路径是否严格处于受控根目录之内，防止路径遍历穿越与软链接逃逸。
 *
 * @param root - 允许的根目录绝对物理地址
 * @param candidate - 待验证的候选路径
 * @returns 规范化后的绝对物理路径
 * @throws Error 当路径逃出根目录或包含非法符号链接时抛出
 */
const within = (root: string, candidate: string): string => {
  const resolvedRoot = path.resolve(root)
  const resolved = path.resolve(candidate)
  if (resolved !== resolvedRoot && !resolved.startsWith(`${resolvedRoot}${path.sep}`)) throw new Error('Path escapes its allowed directory')
  if (existsSync(resolvedRoot) && lstatSync(resolvedRoot).isSymbolicLink()) throw new Error('Symbolic links are not allowed in managed paths')
  let current = resolvedRoot
  for (const part of path.relative(resolvedRoot, resolved).split(path.sep).filter(Boolean)) {
    current = path.join(current, part)
    if (existsSync(current) && lstatSync(current).isSymbolicLink()) throw new Error('Symbolic links are not allowed in managed paths')
  }
  if (existsSync(resolved)) {
    const physical = realpathSync(resolved)
    if (physical !== resolvedRoot && !physical.startsWith(`${resolvedRoot}${path.sep}`)) throw new Error('Path escapes its allowed directory')
  }
  return resolved
}

/**
 * 根据文件扩展名推断 MIME 媒体类型。
 *
 * @param file - 文件名或路径
 * @returns 对应的 MIME 类型字符串
 */
const mime = (file: string): string => ({
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.csv': 'text/csv',
  '.tsv': 'text/tab-separated-values',
  '.json': 'application/json',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
}[path.extname(file).toLowerCase()] ?? 'application/octet-stream')

/** 原子写入 JSON 文件辅助函数 */
const atomicJson = (file: string, value: unknown): void => writeAtomic(file, JSON.stringify(value, null, 2))

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../')
const projectPython = path.join(projectRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

/**
 * 执行项目内置的 Python 校验脚本。
 *
 * @param script - Python 脚本正文
 * @param input - 传递给 stdin 的输入文本
 * @param args - 附加 CLI 参数
 */
const runProjectPython = (script: string, input: string, args: string[] = []): void => {
  const result = spawnSync(projectPython, ['-c', script, ...args], { input, encoding: 'utf8' })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(result.stderr)
}

/**
 * 校验指定 JSON 文本中是否存在重复键。
 *
 * @param source - JSON 文本内容
 * @throws Error 当存在重复键时抛出
 */
const validateJsonDuplicates = (source: string): void => runProjectPython(
  'import json, sys\ndef unique(pairs):\n result = {}\n for key, value in pairs:\n  if key in result: raise ValueError("duplicate key: " + key)\n  result[key] = value\n return result\njson.loads(sys.stdin.read(), object_pairs_hook=unique)',
  source
)

/**
 * 本地数据、项目配置与运行产物存储管理器。
 */
export class DesktopStore {
  /** 当前项目或通用偏好数据根目录绝对物理地址 */
  readonly directory: string
  /** 关联的工作区根目录绝对物理地址 */
  readonly workspace: string
  /** 全局配置目录 */
  readonly globalDirectory: string
  /** 会话附件元数据索引文件 attachments.json 绝对物理路径 */
  private readonly attachmentManifest: string
  /** 生成交付成果元数据索引文件 artifacts.json 绝对物理路径 */
  private readonly artifactManifest: string

  /**
   * 初始化项目与全局存储引擎。
   *
   * @param directory - 项目专属数据存储目录
   * @param workspace - 本地代码工作区目录
   * @param globalDirectory - 全局配置目录
   */
  constructor(directory: string, workspace: string, globalDirectory: string) {
    this.directory = path.resolve(directory)
    this.workspace = path.resolve(workspace)
    this.globalDirectory = path.resolve(globalDirectory)
    mkdirSync(this.directory, { recursive: true })
    mkdirSync(this.workspace, { recursive: true })
    mkdirSync(this.globalDirectory, { recursive: true })
    this.attachmentManifest = path.join(this.directory, 'attachments.json')
    this.artifactManifest = path.join(this.directory, 'artifacts.json')
  }

  /**
   * 导入外部物理文件至工作区内部作为对话附件。
   *
   * 第一步：校验导入数量上限（单次至多 10 个）与单个文件大小上限（25 MiB）。
   * 第二步：为每个文件生成 UUID 并计算工作区相对存储路径 attachments/{id}/{filename}。
   * 第三步：原子复制物理文件至工作区隔离目录，并更新 attachments.json 索引清单。
   *
   * @param paths - 待导入的本地文件绝对路径列表
   * @returns 生成的附件元数据引用列表
   * @throws Error 当文件数超限、文件丢失或文件体积超标时抛出
   */
  importAttachments(paths: string[]): AttachmentRef[] {
    // 第一步：参数与边界限制检查
    if (paths.length > 10) throw new Error('At most 10 files can be imported at once')
    const refs = paths.map(source => {
      const stat = statSync(source)
      if (!stat.isFile()) throw new Error(`Not a file: ${source}`)
      if (stat.size > 25 * 1024 * 1024) throw new Error(`${path.basename(source)} exceeds the 25 MiB attachment limit`)
      const id = randomUUID()
      const name = path.basename(source)
      const relative_path = path.posix.join('attachments', id, name)
      return { id, workspace_id: path.basename(this.directory), name, media_type: mime(name), size: stat.size, relative_path }
    })

    // 第二步：将附件写入工作区并更新清单
    const manifest = this.readJson<Record<string, AttachmentRef>>(this.attachmentManifest, {})
    for (let i = 0; i < refs.length; i++) {
      const ref = refs[i]
      const target = within(this.workspace, path.join(this.workspace, ...ref.relative_path.split('/')))
      mkdirSync(path.dirname(target), { recursive: true })
      copyFileSync(paths[i], target)
      manifest[ref.id] = ref
    }
    if (refs.length) atomicJson(this.attachmentManifest, manifest)
    return refs
  }

  /**
   * 根据附件 ID 安全解析并校验其在本地工作区的绝对物理路径。
   *
   * @param id - 附件唯一 ID
   * @returns 校验通过的工作区物理文件绝对路径
   * @throws Error 当附件不存在或物理文件缺失时抛出
   */
  resolveAttachment(id: string): string {
    const ref = this.attachment(id)
    const resolved = within(this.workspace, path.join(this.workspace, ...ref.relative_path.split('/')))
    if (!existsSync(resolved) || !statSync(resolved).isFile()) throw new Error('Attachment file is missing')
    return resolved
  }

  /**
   * 从附件清单中读取指定 ID 的附件元数据对象并校验格式完整性。
   *
   * @param id - 附件 ID
   * @returns 附件元数据对象
   * @throws Error 当附件未在清单中注册或不属于当前工作区时抛出
   */
  attachment(id: string): AttachmentRef {
    const index = this.readJson<Record<string, AttachmentRef>>(this.attachmentManifest, {})
    const ref = index[id]
    if (!ref || ref.workspace_id !== path.basename(this.directory)) throw new Error('Attachment does not belong to this workspace')
    if (Object.keys(ref).sort().join(',') !== 'id,media_type,name,relative_path,size,workspace_id' || ref.id !== id || !Number.isSafeInteger(ref.size) || ref.size < 0 || !ref.relative_path.startsWith('attachments/')) throw new Error('Malformed attachment reference')
    const file = within(this.workspace, path.join(this.workspace, ...ref.relative_path.split('/')))
    if (!existsSync(file) || !statSync(file).isFile()) throw new Error('Attachment file is missing')
    return ref
  }

  /**
   * 读取指定附件的 Base64 编码数据及 MIME 类型（供前端预览或展示）。
   *
   * @param id - 附件 ID
   * @returns 包含 media_type 与 base64 数据字符串的对象
   */
  readAttachment(id: string): { media_type: string; data: string } {
    const ref = this.attachment(id)
    return { media_type: ref.media_type, data: readFileSync(this.resolveAttachment(id)).toString('base64') }
  }

  /**
   * 获取当前项目已发布的所有交付物成果引用列表。
   *
   * @returns 交付物成果数组
   */
  listArtifacts(): ArtifactRef[] {
    return this.artifactRefs()
  }

  /**
   * 根据虚拟成果路径（如 `/workspace/report.md`）安全解析对应的本地物理文件绝对路径。
   *
   * @param filePath - 虚拟成果文件路径
   * @returns 本地文件绝对物理路径
   * @throws Error 当成果尚未登记发布或物理文件缺失时抛出
   */
  resolveArtifact(filePath: string): string {
    const ref = this.artifactRefs().find(item => item.relative_path === filePath)
    if (!ref) throw new Error('Artifact has not been published')
    const file = this.artifactPath(ref.relative_path)
    if (!existsSync(file) || !statSync(file).isFile()) throw new Error('Artifact file is missing')
    return file
  }

  /**
   * 读取指定交付成果文件的 Base64 编码数据及 MIME 类型。
   *
   * @param filePath - 虚拟成果路径
   * @returns 包含 media_type 与 base64 字符串的对象
   */
  readArtifact(filePath: string): { media_type: string; data: string } {
    const file = this.resolveArtifact(filePath)
    return { media_type: mime(file), data: readFileSync(file).toString('base64') }
  }

  /**
   * 将智能体在工作区内新生成的目标文件登记发布为正式交付物成果。
   *
   * @param relativePath - 虚拟工作区成果相对路径（如 `/workspace/report.md`）
   * @param sourceRunId - 产出此成果的运行轮次 ID
   * @returns 生成并持久化的成果引用对象
   * @throws Error 当物理文件不存在时抛出
   */
  publishArtifact(relativePath: string, sourceRunId: string): ArtifactRef {
    const file = this.artifactPath(relativePath)
    if (!existsSync(file) || !statSync(file).isFile()) throw new Error('Artifact file is missing')
    const ref: ArtifactRef = { id: randomUUID(), workspace_id: path.basename(this.directory), name: path.basename(file), media_type: mime(file), relative_path: relativePath, source_run_id: sourceRunId }
    atomicJson(this.artifactManifest, [...this.artifactRefs(), ref])
    return ref
  }

  /**
   * 读取已被用户授权免二次确认调用的插件 ID 白名单。
   *
   * @returns 授权插件 ID 字符串数组
   */
  authorizedPlugins(): string[] {
    const grants = this.readJson<unknown>(path.join(this.globalDirectory, 'plugin-authorizations.json'), [])
    if (!Array.isArray(grants) || grants.some(id => typeof id !== 'string')) throw new Error('Invalid plugin authorizations')
    return grants
  }

  /**
   * 追加记住插件免二次确认授权。
   *
   * @param ids - 批准授权的插件 ID 列表
   */
  authorizePlugins(ids: string[]): void {
    atomicJson(path.join(this.globalDirectory, 'plugin-authorizations.json'), [...new Set([...this.authorizedPlugins(), ...ids])])
  }

  /**
   * 检查并组合生成当前生效的 Agent 提示词规则、Skills 技能及 MCP 服务配置视图。
   * 支持多层级层叠覆盖（私有配置 > 工作区 .medpeer > 账号全局配置）。
   *
   * @param scope - 配置查询范围 ('global' 全局或 'project' 项目)
   * @returns 聚合计算后的结构化配置视图对象
   */
  inspectConfig(scope: ConfigScope): ConfigView {
    const layers = this.configLayers(scope)
    const agentPath = layers.map(dir => path.join(dir, 'agent.md')).find(file => existsSync(file))
    const servers: Record<string, Json> = {}
    const sources: Record<string, string> = {}
    for (const dir of layers) {
      const file = path.join(dir, 'mcp.json')
      if (!existsSync(file)) continue
      const content = readFileSync(file, 'utf8')
      validateJsonDuplicates(content)
      const parsed = this.validateMcp(JSON.parse(content))
      for (const [id, config] of Object.entries(parsed.mcpServers)) if (!(id in servers)) { servers[id] = config; sources[id] = file }
    }
    const skills = new Map<string, ConfigView['skills'][number]>()
    const enabled = new Map<string, boolean>()
    for (const dir of scope === 'global' ? layers : [...layers].reverse()) {
      const settingsFile = path.join(dir, 'skill-settings.json')
      if (existsSync(settingsFile)) for (const [id, value] of Object.entries(JSON.parse(readFileSync(settingsFile, 'utf8')) as Record<string, boolean>)) enabled.set(id, value)
    }
    for (const dir of layers) {
      const root = path.join(dir, 'skills')
      if (!existsSync(root)) continue
      for (const id of readdirSync(root).sort()) {
        const skillRoot = path.join(root, id)
        if (!statSync(skillRoot).isDirectory() || skills.has(id)) continue
        const skillFile = path.join(skillRoot, 'SKILL.md')
        this.skillId(readFileSync(skillFile, 'utf8'), id)
        skills.set(id, { id, path: skillRoot, source: dir, enabled: enabled.get(id) ?? true })
      }
    }
    const privateDir = path.join(this.directory, 'config')
    return {
      agent: { content: agentPath ? readFileSync(agentPath, 'utf8') : '', source: agentPath ?? '' },
      skills: [...skills.values()],
      mcp: { servers, sources },
      overrides: {
        agent: scope === 'project' && existsSync(path.join(privateDir, 'agent.md')) ? path.join(privateDir, 'agent.md') : '',
        mcp: scope === 'project' && existsSync(path.join(privateDir, 'mcp.json')) ? path.join(privateDir, 'mcp.json') : ''
      }
    }
  }

  /**
   * 保存指定范围下的自定义指令 agent.md 或 MCP 服务配置 mcp.json。
   *
   * @param scope - 目标配置作用域 ('global' 或 'project')
   * @param kind - 配置类型 ('agent' 提示词规则或 'mcp' 外部服务)
   * @param content - 文件正文内容
   * @returns 保存并重新计算后的最新配置视图
   */
  saveConfig(scope: ConfigScope, kind: 'agent' | 'mcp', content: string): ConfigView {
    const directory = this.configWriteDir(scope)
    const target = within(directory, path.join(directory, kind === 'agent' ? 'agent.md' : 'mcp.json'))
    if (kind === 'mcp') {
      validateJsonDuplicates(content)
      this.validateMcp(JSON.parse(content))
    }
    writeAtomic(target, content)
    return this.inspectConfig(scope)
  }

  /**
   * 移除当前项目的局部配置重载（回退至全局配置）。
   *
   * @param kind - 需移除的配置类型 ('agent' 或 'mcp')
   * @returns 移除后的最新配置视图
   */
  removeConfig(kind: 'agent' | 'mcp'): ConfigView {
    const target = path.join(this.directory, 'config', kind === 'agent' ? 'agent.md' : 'mcp.json')
    within(path.join(this.directory, 'config'), target)
    unlinkSync(target)
    return this.inspectConfig('project')
  }

  /**
   * 读取指定技能目录下的 SKILL.md 规则正文。
   *
   * @param scope - 技能所在配置范围
   * @param id - 技能标识 ID
   * @returns 技能 Markdown 正文与来源文件物理路径
   * @throws Error 当技能不存在时抛出
   */
  readSkill(scope: ConfigScope, id: string): { content: string; source: string } {
    this.validateSkillId(id)
    const skill = this.inspectConfig(scope).skills.find(item => item.id === id)
    if (!skill) throw new Error('Skill does not exist')
    const source = path.join(skill.path, 'SKILL.md')
    return { content: readFileSync(source, 'utf8'), source }
  }

  /**
   * 保存并更新指定技能的 SKILL.md 文件正文。
   * 必须包含合法的 YAML frontmatter 且 name 与目录 ID 严格匹配。
   *
   * @param scope - 技能作用域
   * @param id - 技能唯一标识 ID
   * @param content - 待保存的 SKILL.md 内容
   * @returns 保存后的最新配置视图
   */
  saveSkill(scope: ConfigScope, id: string, content: string): ConfigView {
    this.skillId(content, id)
    this.readSkill(scope, id)
    const directory = this.configWriteDir(scope)
    within(directory, path.join(directory, 'skills', id, 'SKILL.md'))
    const target = within(path.join(directory, 'skills', id), path.join(directory, 'skills', id, 'SKILL.md'))
    mkdirSync(path.dirname(target), { recursive: true })
    writeAtomic(target, content)
    return this.inspectConfig(scope)
  }

  /**
   * 切换指定技能的启用或停用状态，并在 skill-settings.json 中持久化。
   *
   * @param scope - 技能作用域
   * @param id - 技能 ID
   * @param enabled - 是否启用
   * @returns 更新后的最新配置视图
   */
  setSkill(scope: ConfigScope, id: string, enabled: boolean): ConfigView {
    this.validateSkillId(id)
    const directory = this.configWriteDir(scope)
    const file = within(directory, path.join(directory, 'skill-settings.json'))
    const settings = this.readJson<Record<string, boolean>>(file, {})
    settings[id] = enabled
    atomicJson(file, settings)
    return this.inspectConfig(scope)
  }

  /**
   * 从外部本地目录导入技能至当前范围。
   * 严格校验技能目录结构、禁止软链接、检查 SKILL.md 存在且 frontmatter 与目录同名。
   *
   * @param scope - 导入目标作用域 ('global' 或 'project')
   * @param source - 外部技能文件夹绝对路径
   * @returns 导入后的最新配置视图
   * @throws Error 当技能已存在、包含符号链接或结构不符合规范时抛出
   */
  importSkill(scope: ConfigScope, source: string): ConfigView {
    if (existsSync(source) && statSync(source).isFile()) source = path.dirname(source)
    const root = path.resolve(source)
    if (lstatSync(root).isSymbolicLink() || !statSync(root).isDirectory()) throw new Error('Skill source must be a directory without a symlink root')
    const id = path.basename(root)
    const content = readFileSync(path.join(root, 'SKILL.md'), 'utf8')
    this.skillId(content, id)
    const inspect = (directory: string): void => {
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const item = path.join(directory, entry.name)
        if (entry.isSymbolicLink()) throw new Error('Skill contains a symbolic link')
        if (entry.isDirectory()) inspect(item)
      }
    }
    inspect(root)
    const directory = this.configWriteDir(scope)
    within(directory, path.join(directory, 'skills', id))
    const destination = path.join(directory, 'skills', id)
    if (existsSync(destination)) throw new Error('Skill already exists in this scope')
    const skillsRoot = path.dirname(destination)
    mkdirSync(skillsRoot, { recursive: true })
    const temporary = path.join(skillsRoot, `.import-${randomUUID()}`)
    const copy = (from: string, to: string): void => {
      mkdirSync(to, { recursive: true })
      for (const entry of readdirSync(from, { withFileTypes: true })) {
        const src = path.join(from, entry.name)
        const dst = within(temporary, path.join(to, entry.name))
        if (entry.isDirectory()) copy(src, dst)
        else copyFileSync(src, dst)
      }
    }
    copy(root, temporary)
    renameSync(temporary, destination)
    return this.inspectConfig(scope)
  }

  /**
   * 读取当前账号设置文件 settings.json，若不存在则回退至默认设置。
   *
   * @returns 桌面客户端通用偏好配置
   */
  readSettings(): DesktopSettings {
    const settings = this.readJson<DesktopSettings>(path.join(this.globalDirectory, 'settings.json'), defaults)
    return { ...settings, plugins: { ...defaults.plugins, ...settings.plugins } }
  }

  /**
   * 增量合并并原子保存偏好设置项。
   *
   * @param patch - 偏好配置局部变更对象
   */
  saveSettings(patch: Partial<DesktopSettings>): void {
    atomicJson(path.join(this.globalDirectory, 'settings.json'), {
      ...this.readSettings(),
      ...patch,
      runtimes: { ...this.readSettings().runtimes, ...patch.runtimes }
    })
  }

  /**
   * 记录单次模型调用的 Token 消耗与计费区间估算至 usage.json。
   *
   * @param projectId - 归属项目 ID
   * @param sessionId - 归属会话 ID
   * @param record - 单次调用用量记录
   */
  recordUsage(projectId: string, sessionId: string, record: UsageRecord): void {
    const file = path.join(this.globalDirectory, 'usage.json')
    const records = this.readJson<Array<UsageRecord & { project_id: string; session_id: string }>>(file, [])
    const index = records.findIndex(item => item.request_id === record.request_id)
    if (index >= 0) {
      if (records[index].usage || !record.usage) return
      records[index] = { ...records[index], ...record, requested_at: records[index].requested_at, cost_estimate: record.cost_estimate ?? records[index].cost_estimate }
    } else {
      records.push({ ...record, project_id: projectId, session_id: sessionId })
    }
    atomicJson(file, records)
  }

  /**
   * 统计聚合指定周期与模型维度的 Token 消耗、缓存命中与费用研值区间报表。
   *
   * @param period - 时间周期前缀（如 "2026-10"、"2026-10-16" 或 "" 表示全部）
   * @param model - 筛选的模型名称（空字符串表示不限制模型）
   * @param session - 可选的项目与会话限定范围
   * @returns 聚合完成的结构化用量统计报表
   */
  usageStats(period: string, model: string, session?: { projectId: string; sessionId: string }): UsageStats {
    const all = this.readJson<Array<UsageRecord & { project_id: string; session_id: string }>>(path.join(this.globalDirectory, 'usage.json'), [])
      .filter(item => !session || (item.project_id === session.projectId && item.session_id === session.sessionId))
    const dates = all.map(item => new Date(item.requested_at).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' }))
    const selected = all.filter((item, i) => dates[i].startsWith(period) && (!model || item.model === model))

    // 内部汇总计算函数
    const summarize = (items: typeof all) => {
      const usages = items.flatMap(item => item.usage ? [item.usage] : [])
      const sum = (key: 'input_tokens' | 'output_tokens' | 'total_tokens') => usages.reduce((total, usage) => total + usage[key], 0)
      const costs = items.flatMap(item => item.source === 'jev' || !item.cost_estimate ? [] : [item.cost_estimate])
      const cost_estimate = costs.length ? {
        minimum_cny: decimalSum(costs.map(v => v.minimum_cny)),
        maximum_cny: decimalSum(costs.map(v => v.maximum_cny)),
        minimum_credits: decimalSum(costs.map(v => v.minimum_credits)),
        maximum_credits: decimalSum(costs.map(v => v.maximum_credits)),
        rate_dates: [...new Set(costs.flatMap(v => v.rate_dates))].sort()
      } : null
      return {
        requests: items.length,
        reported_requests: usages.length,
        input_tokens: sum('input_tokens'),
        output_tokens: sum('output_tokens'),
        total_tokens: sum('total_tokens'),
        cache_read: usages.length === items.length && usages.every(v => v.input_token_details?.cache_read != null) ? usages.reduce((n, v) => n + v.input_token_details!.cache_read!, 0) : null,
        reasoning: usages.length === items.length && usages.every(v => v.output_token_details?.reasoning != null) ? usages.reduce((n, v) => n + v.output_token_details!.reasoning!, 0) : null,
        cost_estimate,
        unpriced_requests: items.filter(v => v.source !== 'jev' && !v.cost_estimate).length,
        jev_requests: items.filter(v => v.source === 'jev').length
      }
    }

    // 分组辅助函数
    const group = (items: typeof all, key: (item: typeof all[number]) => string) => [...new Set(items.map(key))].sort().map(id => ({ id, items: items.filter(item => key(item) === id) }))
    const modelRows = group(selected, item => `${item.model}\0${item.source}`).map(({ id, items }) => {
      const [name, source] = id.split('\0')
      return { model: name, source: source as 'model' | 'jev', ...summarize(items) }
    })
    const dailyRows = group(selected, item => `${new Date(item.requested_at).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })}\0${item.model}\0${item.source}`).reverse()

    return {
      timezone: 'Asia/Shanghai',
      today: new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' }),
      tracking_since: dates.length ? [...dates].sort()[0] : null,
      dates: [...new Set(dates)].sort(),
      model_names: [...new Set(all.map(v => v.model))].sort(),
      totals: summarize(selected),
      models: modelRows,
      daily: dailyRows.map(({ id, items }) => {
        const [date, name, source] = id.split('\0')
        return { date, model: name, source: source as 'model' | 'jev', ...summarize(items) }
      })
    } as UsageStats
  }

  /** 读取已发布的成果清单并严格校验各字段格式 */
  private artifactRefs(): ArtifactRef[] {
    const refs = this.readJson<ArtifactRef[]>(this.artifactManifest, [])
    for (const ref of refs) {
      if (Object.keys(ref).sort().join(',') !== 'id,media_type,name,relative_path,source_run_id,workspace_id' || ref.workspace_id !== path.basename(this.directory) || !ref.id || !ref.name || !ref.media_type || !ref.source_run_id || !ref.relative_path.startsWith('/workspace/')) throw new Error('Malformed artifact reference')
      this.artifactPath(ref.relative_path)
    }
    return refs
  }

  /** 将虚拟成果路径转换为工作区物理文件路径 */
  private artifactPath(relative: string): string {
    if (!relative.startsWith('/workspace/')) throw new Error('Artifact path must use /workspace/ prefix')
    return within(this.workspace, path.join(this.workspace, relative.slice('/workspace/'.length)))
  }

  /** 获取指定范围的配置查找目录层叠顺序列表 */
  private configLayers(scope: ConfigScope): string[] {
    if (scope === 'global') return [path.join(this.globalDirectory, 'config')]
    if (scope !== 'project') throw new Error('Invalid config scope')
    return [path.join(this.directory, 'config'), ...(this.workspace ? [path.join(this.workspace, '.medpeer')] : []), path.join(this.globalDirectory, 'config')]
  }

  /** 获取指定范围配置持久化写入的目标目录 */
  private configWriteDir(scope: ConfigScope): string {
    if (scope === 'global') return path.join(this.globalDirectory, 'config')
    if (scope === 'project') return path.join(this.directory, 'config')
    throw new Error('Invalid config scope')
  }

  /** 校验技能 ID 的命名规则（小写字母、数字及连字符） */
  private validateSkillId(id: string): void {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) || id.length > 64) throw new Error('Invalid skill ID')
  }

  /** 校验技能 Markdown 正文的 YAML frontmatter 与目录名称一致 */
  private skillId(content: string, expected: string): void {
    this.validateSkillId(expected)
    const match = content.match(/^---\s*\r?\n([\s\S]*?)\r?\n---/)
    if (!match) throw new Error('Skill frontmatter is required')
    const id = match[1].match(/^name:\s*([A-Za-z0-9_-]+)\s*$/m)?.[1]
    if (id !== expected) throw new Error('Skill name must match its directory')
  }

  /** 深度校验 mcp.json 服务的协议结构合法性 */
  private validateMcp(value: unknown): { mcpServers: Record<string, Json> } {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== 1 || !('mcpServers' in value) || !value.mcpServers || typeof value.mcpServers !== 'object' || Array.isArray(value.mcpServers)) throw new Error('Invalid MCP configuration')
    const servers = value.mcpServers as Record<string, Json>
    for (const [id, raw] of Object.entries(servers)) {
      if (!id || !raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(`Invalid MCP server: ${id}`)
      const server = raw as Record<string, Json>
      if (server.transport === 'stdio') {
        const allowed = ['transport', 'command', 'args', 'env', 'enabled']
        if (!('command' in server) || typeof server.command !== 'string' || !server.command || Object.keys(server).some(key => !allowed.includes(key)) || ('args' in server && (!Array.isArray(server.args) || server.args.some(arg => typeof arg !== 'string'))) || ('env' in server && (!server.env || typeof server.env !== 'object' || Array.isArray(server.env) || Object.values(server.env).some(item => typeof item !== 'string'))) || ('enabled' in server && typeof server.enabled !== 'boolean')) throw new Error(`Invalid stdio MCP server: ${id}`)
      } else if (server.transport === 'http' || server.transport === 'streamable-http' || server.transport === 'sse') {
        const allowed = ['transport', 'url', 'headers', 'enabled']
        let validUrl = false
        if (typeof server.url === 'string') { const url = new URL(server.url); validUrl = ['http:', 'https:'].includes(url.protocol) && Boolean(url.host) }
        if (!validUrl || Object.keys(server).some(key => !allowed.includes(key)) || ('headers' in server && (!server.headers || typeof server.headers !== 'object' || Array.isArray(server.headers) || Object.values(server.headers).some(item => typeof item !== 'string'))) || ('enabled' in server && typeof server.enabled !== 'boolean')) throw new Error(`Invalid HTTP MCP server: ${id}`)
      } else throw new Error(`Unsupported MCP transport: ${id}`)
    }
    return value as { mcpServers: Record<string, Json> }
  }

  /** 安全读取 JSON 文件并反序列化，文件不存在时返回 fallback 兜底值 */
  private readJson<T>(file: string, fallback: T): T {
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) as T : fallback
  }
}

