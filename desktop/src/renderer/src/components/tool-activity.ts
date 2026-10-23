import type { RunRecord } from '../../../shared/protocol'

export function formatDuration(elapsed?: number): string {
  if (typeof elapsed !== 'number' || !Number.isFinite(elapsed)) return ''
  const seconds = Math.max(0, Math.round(elapsed))
  return `用时 ${seconds >= 60 ? `${Math.floor(seconds / 60)}分` : ''}${seconds % 60}秒`
}

export function terminalOutput(data: unknown): string | null {
  if (typeof data === 'string') {
    try { data = JSON.parse(data) } catch { return null }
  }
  if (!data || typeof data !== 'object' || !('output' in data) || typeof data.output !== 'string') return null
  const notes = []
  if ('exit_code' in data && typeof data.exit_code === 'number') notes.push(`退出码 ${data.exit_code}`)
  if ('timed_out' in data && data.timed_out === true) notes.push('执行超时')
  if ('truncated' in data && data.truncated === true) notes.push('输出已截断，完整日志见执行元数据')
  return data.output + (notes.length ? `\n\n${notes.join(' · ')}` : '')
}

export function describeTool(record: RunRecord) {
  const name = record.name || record.node || record.kind
  const args = record.arguments && typeof record.arguments === 'object' && !Array.isArray(record.arguments) ? record.arguments : {}
  const preview = [args.command, args.cmd, args.path, args.file_path, args.query, args.pattern].find(value => typeof value === 'string')
  const path = typeof (args.file_path ?? args.path) === 'string' ? String(args.file_path ?? args.path) : ''
  const skill = /read|cat/i.test(name) && path.replace(/\\/g, '/').match(/\/skills\/([^/]+)\/SKILL\.md$/i)?.[1]
  const verb = skill ? '读取技能' : /write|edit|patch/i.test(name) ? '编辑' : /read|cat/i.test(name) ? '读取' : /search|find|grep/i.test(name) ? '搜索' : '运行'
  const command = /exec|shell|terminal|repl|cmd/i.test(name)
  return { name, verb, skill, command, preview: skill || preview || record.summary || name }
}

export function toolSteps(records: RunRecord[]): RunRecord[] {
  const tools = new Map<string, RunRecord>()
  records.filter(record => ['tool_start', 'tool_end', 'tool_error'].includes(record.kind)).forEach((record, index) => {
    const key = record.call_id ?? record.operation_id ?? String(index)
    tools.set(key, { ...tools.get(key), ...record })
  })
  return [...tools.values()]
}
export function stepStatus(step: RunRecord, workflowStatus: string): string {
  const currentStatus = step.status ?? (step.kind === 'tool_start' ? 'running' : step.kind)
  if (currentStatus === 'running' && workflowStatus !== 'running') {
    return ['complete', 'completed', 'success'].includes(workflowStatus) ? 'interrupted' : workflowStatus
  }
  return currentStatus
}
