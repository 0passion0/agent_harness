import type { RunRecord } from '../../../shared/protocol'
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
