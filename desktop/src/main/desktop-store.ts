/**
 * @file desktop-store.ts
 * @description 账号与项目级本地数据管理存储引擎。
 * 负责模型、解释器、权限与 Agent 指令配置、
 * 附件与产物管理，以及 Token 用量统计。
 * @author tongliga
 * @date 2026-10-16
 */

import { createHash, randomUUID } from 'node:crypto'
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, statSync, unlinkSync } from 'node:fs'
import path from 'node:path'
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
  inspectConfig(scope: ConfigScope): ConfigView {
    const agentPath = this.configLayers(scope).map(dir => path.join(dir, 'agent.md')).find(file => existsSync(file))
    const override = path.join(this.directory, 'config', 'agent.md')
    return { agent: { content: agentPath ? readFileSync(agentPath, 'utf8') : '', source: agentPath ?? '' }, overrides: { agent: scope === 'project' && existsSync(override) ? override : '' } }
  }
  saveConfig(scope: ConfigScope, kind: 'agent', content: string): ConfigView {
    if (kind !== 'agent') throw new Error('Only agent instructions are supported')
    const directory = this.configWriteDir(scope)
    writeAtomic(within(directory, path.join(directory, 'agent.md')), content)
    return this.inspectConfig(scope)
  }
  removeConfig(kind: 'agent'): ConfigView {
    if (kind !== 'agent') throw new Error('Only agent instructions are supported')
    const directory = path.join(this.directory, 'config')
    unlinkSync(within(directory, path.join(directory, 'agent.md')))
    return this.inspectConfig('project')
  }

  /**
   * 读取当前账号设置文件 settings.json，若不存在则回退至默认设置。
   *
   * @returns 桌面客户端通用偏好配置
   */
  readSettings(): DesktopSettings {
    const settings = this.readJson<DesktopSettings>(path.join(this.globalDirectory, 'settings.json'), defaults)
    return { model_code: settings.model_code, runtimes: settings.runtimes, permission_mode: settings.permission_mode, max_context_tokens: settings.max_context_tokens }
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

  /** 安全读取 JSON 文件并反序列化，文件不存在时返回 fallback 兜底值 */
  private readJson<T>(file: string, fallback: T): T {
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) as T : fallback
  }
}

