import { createHash, randomUUID } from 'node:crypto'
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, statSync, unlinkSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { ArtifactRef, AttachmentRef, ConfigScope, ConfigView, Json, UsageStats } from '../shared/protocol'
import { writeAtomic } from './local-store'

type DesktopSettings = {
  model_code: string
  runtimes: { python: string; node: string }
  permission_mode: 'read_only' | 'ask' | 'full'
  max_context_tokens: number
  plugins: Record<string, boolean>
}
type UsageRecord = { request_id: string; requested_at: string; model: string; source: 'model' | 'jev'; usage: { input_tokens: number; output_tokens: number; total_tokens: number; input_token_details?: { cache_read?: number }; output_token_details?: { reasoning?: number } } | null; cost_estimate: { minimum_cny: string; maximum_cny: string; minimum_credits: string; maximum_credits: string; rate_dates: string[] } | null }
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

const defaults: DesktopSettings = { model_code: '', runtimes: { python: '', node: '' }, permission_mode: 'ask', max_context_tokens: 128000, plugins: {} }
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
const mime = (file: string): string => ({ '.txt': 'text/plain', '.md': 'text/markdown', '.csv': 'text/csv', '.tsv': 'text/tab-separated-values', '.json': 'application/json', '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }[path.extname(file).toLowerCase()] ?? 'application/octet-stream')
const atomicJson = (file: string, value: unknown): void => writeAtomic(file, JSON.stringify(value, null, 2))
const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../')
const projectPython = path.join(projectRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const runProjectPython = (script: string, input: string, args: string[] = []): void => {
  const result = spawnSync(projectPython, ['-c', script, ...args], { input, encoding: 'utf8' })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(result.stderr)
}
const validateJsonDuplicates = (source: string): void => runProjectPython(
  'import json, sys\ndef unique(pairs):\n result = {}\n for key, value in pairs:\n  if key in result: raise ValueError("duplicate key: " + key)\n  result[key] = value\n return result\njson.loads(sys.stdin.read(), object_pairs_hook=unique)',
  source
)

/** Local, account-scoped desktop data used by renderer features independent of the agent engine. */
export class DesktopStore {
  readonly directory: string
  readonly workspace: string
  readonly accountDirectory: string
  private readonly attachmentManifest: string
  private readonly artifactManifest: string

  constructor(directory: string, workspace: string, accountDirectory: string) {
    this.directory = path.resolve(directory)
    this.workspace = path.resolve(workspace)
    this.accountDirectory = path.resolve(accountDirectory)
    mkdirSync(this.directory, { recursive: true })
    mkdirSync(this.workspace, { recursive: true })
    mkdirSync(this.accountDirectory, { recursive: true })
    this.attachmentManifest = path.join(this.directory, 'attachments.json')
    this.artifactManifest = path.join(this.directory, 'artifacts.json')
  }

  importAttachments(paths: string[]): AttachmentRef[] {
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

  resolveAttachment(id: string): string {
    const ref = this.attachment(id)
    const resolved = within(this.workspace, path.join(this.workspace, ...ref.relative_path.split('/')))
    if (!existsSync(resolved) || !statSync(resolved).isFile()) throw new Error('Attachment file is missing')
    return resolved
  }

  attachment(id: string): AttachmentRef {
    const index = this.readJson<Record<string, AttachmentRef>>(this.attachmentManifest, {})
    const ref = index[id]
    if (!ref || ref.workspace_id !== path.basename(this.directory)) throw new Error('Attachment does not belong to this workspace')
    if (Object.keys(ref).sort().join(',') !== 'id,media_type,name,relative_path,size,workspace_id' || ref.id !== id || !Number.isSafeInteger(ref.size) || ref.size < 0 || !ref.relative_path.startsWith('attachments/')) throw new Error('Malformed attachment reference')
    const file = within(this.workspace, path.join(this.workspace, ...ref.relative_path.split('/')))
    if (!existsSync(file) || !statSync(file).isFile()) throw new Error('Attachment file is missing')
    return ref
  }

  readAttachment(id: string): { media_type: string; data: string } {
    const ref = this.attachment(id)
    return { media_type: ref.media_type, data: readFileSync(this.resolveAttachment(id)).toString('base64') }
  }

  listArtifacts(): ArtifactRef[] {
    return this.artifactRefs()
  }

  resolveArtifact(filePath: string): string {
    const ref = this.artifactRefs().find(item => item.relative_path === filePath)
    if (!ref) throw new Error('Artifact has not been published')
    const file = this.artifactPath(ref.relative_path)
    if (!existsSync(file) || !statSync(file).isFile()) throw new Error('Artifact file is missing')
    return file
  }

  readArtifact(filePath: string): { media_type: string; data: string } {
    const file = this.resolveArtifact(filePath)
    return { media_type: mime(file), data: readFileSync(file).toString('base64') }
  }

  publishArtifact(relativePath: string, sourceRunId: string): ArtifactRef {
    const file = this.artifactPath(relativePath)
    if (!existsSync(file) || !statSync(file).isFile()) throw new Error('Artifact file is missing')
    const ref: ArtifactRef = { id: randomUUID(), workspace_id: path.basename(this.directory), name: path.basename(file), media_type: mime(file), relative_path: relativePath, source_run_id: sourceRunId }
    atomicJson(this.artifactManifest, [...this.artifactRefs(), ref])
    return ref
  }

  authorizedPlugins(): string[] {
    const grants = this.readJson<unknown>(path.join(this.accountDirectory, 'plugin-authorizations.json'), [])
    if (!Array.isArray(grants) || grants.some(id => typeof id !== 'string')) throw new Error('Invalid plugin authorizations')
    return grants
  }

  authorizePlugins(ids: string[]): void {
    atomicJson(path.join(this.accountDirectory, 'plugin-authorizations.json'), [...new Set([...this.authorizedPlugins(), ...ids])])
  }

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
    return { agent: { content: agentPath ? readFileSync(agentPath, 'utf8') : '', source: agentPath ?? '' }, skills: [...skills.values()], mcp: { servers, sources }, overrides: { agent: scope === 'project' && existsSync(path.join(privateDir, 'agent.md')) ? path.join(privateDir, 'agent.md') : '', mcp: scope === 'project' && existsSync(path.join(privateDir, 'mcp.json')) ? path.join(privateDir, 'mcp.json') : '' } }
  }

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

  removeConfig(kind: 'agent' | 'mcp'): ConfigView {
    const target = path.join(this.directory, 'config', kind === 'agent' ? 'agent.md' : 'mcp.json')
    within(path.join(this.directory, 'config'), target)
    unlinkSync(target)
    return this.inspectConfig('project')
  }

  readSkill(scope: ConfigScope, id: string): { content: string; source: string } {
    this.validateSkillId(id)
    const skill = this.inspectConfig(scope).skills.find(item => item.id === id)
    if (!skill) throw new Error('Skill does not exist')
    const source = path.join(skill.path, 'SKILL.md')
    return { content: readFileSync(source, 'utf8'), source }
  }

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

  setSkill(scope: ConfigScope, id: string, enabled: boolean): ConfigView {
    this.validateSkillId(id)
    const directory = this.configWriteDir(scope)
    const file = within(directory, path.join(directory, 'skill-settings.json'))
    const settings = this.readJson<Record<string, boolean>>(file, {})
    settings[id] = enabled
    atomicJson(file, settings)
    return this.inspectConfig(scope)
  }

  importSkill(scope: ConfigScope, source: string): ConfigView {
    if (existsSync(source) && statSync(source).isFile()) source = path.dirname(source)
    const root = path.resolve(source)
    if (lstatSync(root).isSymbolicLink() || !statSync(root).isDirectory()) throw new Error('Skill source must be a directory without a symlink root')
    const id = path.basename(root)
    const content = readFileSync(path.join(root, 'SKILL.md'), 'utf8')
    this.skillId(content, id)
    const inspect = (directory: string): void => { for (const entry of readdirSync(directory, { withFileTypes: true })) { const item = path.join(directory, entry.name); if (entry.isSymbolicLink()) throw new Error('Skill contains a symbolic link'); if (entry.isDirectory()) inspect(item) } }
    inspect(root)
    const directory = this.configWriteDir(scope)
    within(directory, path.join(directory, 'skills', id))
    const destination = path.join(directory, 'skills', id)
    if (existsSync(destination)) throw new Error('Skill already exists in this scope')
    const skillsRoot = path.dirname(destination); mkdirSync(skillsRoot, { recursive: true })
    const temporary = path.join(skillsRoot, `.import-${randomUUID()}`)
    const copy = (from: string, to: string): void => { mkdirSync(to, { recursive: true }); for (const entry of readdirSync(from, { withFileTypes: true })) { const src = path.join(from, entry.name); const dst = within(temporary, path.join(to, entry.name)); if (entry.isDirectory()) copy(src, dst); else copyFileSync(src, dst) } }
    copy(root, temporary)
    renameSync(temporary, destination)
    return this.inspectConfig(scope)
  }

  readSettings(): DesktopSettings { return this.readJson<DesktopSettings>(path.join(this.accountDirectory, 'settings.json'), defaults) }
  saveSettings(patch: Partial<DesktopSettings>): void { atomicJson(path.join(this.accountDirectory, 'settings.json'), { ...this.readSettings(), ...patch, runtimes: { ...this.readSettings().runtimes, ...patch.runtimes } }) }

  recordUsage(projectId: string, sessionId: string, record: UsageRecord): void {
    const file = path.join(this.accountDirectory, 'usage.json')
    const records = this.readJson<Array<UsageRecord & { project_id: string; session_id: string }>>(file, [])
    const index = records.findIndex(item => item.request_id === record.request_id)
    if (index >= 0) {
      if (records[index].usage || !record.usage) return
      records[index] = { ...records[index], ...record, requested_at: records[index].requested_at, cost_estimate: record.cost_estimate ?? records[index].cost_estimate }
    } else records.push({ ...record, project_id: projectId, session_id: sessionId })
    atomicJson(file, records)
  }

  usageStats(period: string, model: string, session?: { projectId: string; sessionId: string }): UsageStats {
    const all = this.readJson<Array<UsageRecord & { project_id: string; session_id: string }>>(path.join(this.accountDirectory, 'usage.json'), []).filter(item => !session || (item.project_id === session.projectId && item.session_id === session.sessionId))
    const dates = all.map(item => new Date(item.requested_at).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' }))
    const selected = all.filter((item, i) => dates[i].startsWith(period) && (!model || item.model === model))
    const summarize = (items: typeof all) => {
      const usages = items.flatMap(item => item.usage ? [item.usage] : [])
      const sum = (key: 'input_tokens' | 'output_tokens' | 'total_tokens') => usages.reduce((total, usage) => total + usage[key], 0)
      const costs = items.flatMap(item => item.source === 'jev' || !item.cost_estimate ? [] : [item.cost_estimate])
      const cost_estimate = costs.length ? { minimum_cny: decimalSum(costs.map(v => v.minimum_cny)), maximum_cny: decimalSum(costs.map(v => v.maximum_cny)), minimum_credits: decimalSum(costs.map(v => v.minimum_credits)), maximum_credits: decimalSum(costs.map(v => v.maximum_credits)), rate_dates: [...new Set(costs.flatMap(v => v.rate_dates))].sort() } : null
      return { requests: items.length, reported_requests: usages.length, input_tokens: sum('input_tokens'), output_tokens: sum('output_tokens'), total_tokens: sum('total_tokens'), cache_read: usages.length === items.length && usages.every(v => v.input_token_details?.cache_read != null) ? usages.reduce((n, v) => n + v.input_token_details!.cache_read!, 0) : null, reasoning: usages.length === items.length && usages.every(v => v.output_token_details?.reasoning != null) ? usages.reduce((n, v) => n + v.output_token_details!.reasoning!, 0) : null, cost_estimate, unpriced_requests: items.filter(v => v.source !== 'jev' && !v.cost_estimate).length, jev_requests: items.filter(v => v.source === 'jev').length }
    }
    const group = (items: typeof all, key: (item: typeof all[number]) => string) => [...new Set(items.map(key))].sort().map(id => ({ id, items: items.filter(item => key(item) === id) }))
    const modelRows = group(selected, item => `${item.model}\0${item.source}`).map(({ id, items }) => { const [name, source] = id.split('\0'); return { model: name, source: source as 'model' | 'jev', ...summarize(items) } })
    const dailyRows = group(selected, item => `${new Date(item.requested_at).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })}\0${item.model}\0${item.source}`).reverse()
    return { timezone: 'Asia/Shanghai', today: new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' }), tracking_since: dates.length ? [...dates].sort()[0] : null, dates: [...new Set(dates)].sort(), model_names: [...new Set(all.map(v => v.model))].sort(), totals: summarize(selected), models: modelRows, daily: dailyRows.map(({ id, items }) => { const [date, name, source] = id.split('\0'); return { date, model: name, source: source as 'model' | 'jev', ...summarize(items) } }) } as UsageStats
  }

  private artifactRefs(): ArtifactRef[] {
    const refs = this.readJson<ArtifactRef[]>(this.artifactManifest, [])
    for (const ref of refs) {
      if (Object.keys(ref).sort().join(',') !== 'id,media_type,name,relative_path,source_run_id,workspace_id' || ref.workspace_id !== path.basename(this.directory) || !ref.id || !ref.name || !ref.media_type || !ref.source_run_id || !ref.relative_path.startsWith('/workspace/')) throw new Error('Malformed artifact reference')
      this.artifactPath(ref.relative_path)
    }
    return refs
  }
  private artifactPath(relative: string): string {
    if (!relative.startsWith('/workspace/')) throw new Error('Artifact path must use /workspace/ prefix')
    return within(this.workspace, path.join(this.workspace, relative.slice('/workspace/'.length)))
  }
  private configLayers(scope: ConfigScope): string[] { if (scope === 'global') return [path.join(this.accountDirectory, 'config')]; if (scope !== 'project') throw new Error('Invalid config scope'); return [path.join(this.directory, 'config'), ...(this.workspace ? [path.join(this.workspace, '.medpeer')] : []), path.join(this.accountDirectory, 'config')] }
  private configWriteDir(scope: ConfigScope): string { if (scope === 'global') return path.join(this.accountDirectory, 'config'); if (scope === 'project') return path.join(this.directory, 'config'); throw new Error('Invalid config scope') }
  private validateSkillId(id: string): void { if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) || id.length > 64) throw new Error('Invalid skill ID') }
  private skillId(content: string, expected: string): void { this.validateSkillId(expected); const match = content.match(/^---\s*\r?\n([\s\S]*?)\r?\n---/); if (!match) throw new Error('Skill frontmatter is required'); const id = match[1].match(/^name:\s*([A-Za-z0-9_-]+)\s*$/m)?.[1]; if (id !== expected) throw new Error('Skill name must match its directory') }
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
  private readJson<T>(file: string, fallback: T): T { return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) as T : fallback }
}
