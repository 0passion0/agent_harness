/**
 * @file commands.ts
 * @description 桌面端 IPC 指令校验、权限鉴权与安全辅助函数模块。
 * 包含受信任渲染进程发送方断言、JSON-RPC 命令协议严格白名单校验、
 * 物理文件路径数组断言及虚拟成果文件路径打开等能力。
 * @author tongliga
 * @date 2026-10-16
 */

import type { Command } from '../shared/protocol'

/**
 * 允许前端调用的合法 JSON-RPC 方法名及其必填参数字段白名单映射表。
 * 参数键不在白名单中的请求将被直接拦截，防止参数注入。
 */
const methods: Record<string, string[]> = {
  'usage.stats': ['period', 'model'],
  'protocol.hello': [], 'session.snapshot': [], 'session.list': [], 'session.token_usage': [], 'session.search': ['query'], 'session.clear': [], 'app.snapshot': [], 'app.environment': [],
  'session.resume': ['session_id'],
  'run.start': ['text'], 'run.attachments': ['text', 'attachment_ids'], 'run.stop': ['run_id'],
  'interaction.answer': ['run_id', 'interaction_id', 'answer'], 'project.create': ['name'], 'project.select': ['project_id'],
  'model.test': [], 'model.configure': ['model_code'], 'context.configure': ['max_tokens'],
  'runtime.configure': ['python', 'node'],
  'permission.set': ['mode'],
  'harness.projects': [], 'harness.inspect': ['scope', 'project_id'], 'harness.save': ['scope', 'project_id', 'kind', 'name', 'content'],
  'harness.remove': ['scope', 'project_id', 'kind', 'name'],
  'session.rename': ['session_id', 'title'], 'session.delete': ['session_id'], 'session.export': ['session_id']
}

/**
 * 校验 IPC 消息发送方 URL 的可信度，防止伪造 IPC 攻击。
 *
 * @param actual - 实际发送方的 frame URL
 * @param trusted - 预期可信的本地 index.html 文件 URL 或开发服务器 URL
 * @throws Error 当发送方与受信任 URL 不匹配时抛出
 */
export function assertSender(actual: string, trusted: string): void {
  if (actual !== trusted) throw new Error('Untrusted IPC sender')
}

/**
 * 断言并严格校验来自 Renderer 的命令对象是否符合协议格式。
 *
 * 第一步：检查顶层结构是否为纯对象，且键集合恰好为 id, method, params。
 * 第二步：验证方法名是否在 methods 白名单中，且 params 为纯键值对象。
 * 第三步：比对参数键集合与白名单的一致性，校验各参数的数据类型。
 * 第四步：针对特殊方法（如 run.start、run.attachments）执行业务级约束校验。
 * 第五步：针对 Harness 资源管理指令执行配置范围与资源种类的严格枚举断言。
 *
 * @param value - 待校验的原始命令对象
 * @throws Error 当命令结构非法、方法未注册或参数不符合规则时抛出
 * @author tongliga
 * @date 2026-10-22
 */
export function assertCommand(value: unknown): asserts value is Command {
  // 第一步：校验对象容器与顶层键
  if (!value || typeof value !== 'object') throw new Error('Invalid command')
  const command = value as { id?: unknown; method?: unknown; params?: unknown }
  if (Object.keys(value).sort().join(',') !== 'id,method,params') throw new Error('Unexpected command fields')

  // 第二步：校验 id、method 是否合法有效
  if (typeof command.id !== 'string' || !command.id || typeof command.method !== 'string' || !Object.hasOwn(methods, command.method) ||
      !command.params || typeof command.params !== 'object' || Array.isArray(command.params)) throw new Error('Invalid command')

  // 第三步：比对参数键集合与白名单定义
  const params = command.params as Record<string, unknown>
  const expected = methods[command.method]
  if (Object.keys(params).some(key => !expected.includes(key)) || expected.some(key => !(key in params))) throw new Error('Invalid command params')

  // 第四步：检查具体字段类型与特殊指令合法性
  for (const [key, value] of Object.entries(params)) {
    if (command.method === 'run.attachments' && key === 'attachment_ids') {
      if (!Array.isArray(value) || value.length > 10 || value.some(item => typeof item !== 'string')) throw new Error('Invalid attachment IDs')
    } else if (typeof value !== 'string') throw new Error('Invalid command params')
  }
  if (command.method === 'run.start' && !(params.text as string).trim()) throw new Error('Text required')

  // 第五步：针对 Harness 资源管理指令校验配置作用域与资源种类合法性
  if (command.method.startsWith('harness.') && command.method !== 'harness.projects') {
    if (params.scope !== 'global' && params.scope !== 'project') throw new Error('Invalid config scope')
    if (command.method !== 'harness.inspect' && params.kind !== 'agent' && params.kind !== 'skill') throw new Error('Invalid resource kind')
  }
}

/**
 * 断言校验传入的值是否为合法的物理文件路径数组。
 *
 * @param value - 待校验的对象
 * @throws Error 当不是字符串数组时抛出
 */
export function assertFilePaths(value: unknown): asserts value is string[] {
  if (!Array.isArray(value) || value.some(filePath => typeof filePath !== 'string')) throw new Error('Invalid file paths')
}

/**
 * 解析成果虚拟路径并调起系统应用打开交付文件。
 *
 * 第一步：校验虚拟路径安全前缀（必须以 `/workspace/` 开头）。
 * 第二步：调用后端 resolve 命令将虚拟路径解析为本机真实物理路径。
 * 第三步：调用系统 openPath 尝试启动默认应用打开文件并检查结果。
 *
 * @param virtualPath - 交付物虚拟路径（如 `/workspace/report.md`）
 * @param resolve - 后端物理路径解析委托函数
 * @param openPath - 系统原生打开文件委托函数
 * @throws Error 当路径非法、解析失败或系统打开报错时抛出
 */
export async function resolveAndOpenArtifact(
  virtualPath: string,
  resolve: (path: string) => Promise<{ path: string }>,
  openPath: (path: string) => Promise<string>
): Promise<void> {
  // 第一步：安全边界校验
  if (!virtualPath.startsWith('/workspace/')) throw new Error('Invalid artifact path')

  // 第二步：解析为本机物理文件路径
  const result = await resolve(virtualPath)

  // 第三步：唤起系统默认应用打开
  const error = await openPath(result.path)
  if (error) throw new Error(error)
}


/**
 * 校验原始命令对象合法性并强转为类型安全的 Command 协议对象。
 *
 * @param raw - 待校验的原始命令对象
 * @returns 校验通过的合法 Command 对象
 * @throws Error 当命令不符合协议规范时抛出
 */
export function validateCommand(raw: unknown): Command {
  assertCommand(raw)
  return raw
}

