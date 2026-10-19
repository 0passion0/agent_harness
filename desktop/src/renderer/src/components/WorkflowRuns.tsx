import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { RunRecord, Snapshot } from '../../../shared/protocol'

export type WorkflowGroup = { operationId: string; title: string; records: RunRecord[] }

export function workflowSteps(records: RunRecord[]): RunRecord[] {
  const steps = new Map<string, RunRecord>()
  records.filter(record => record.kind === 'workflow.node' || ['tool_start', 'tool_end', 'tool_error'].includes(record.kind)).forEach((record, index) => {
    const key = record.kind === 'workflow.node'
      ? `node:${record.operation_id ?? record.seq ?? index}`
      : `tool:${record.operation_id ?? record.call_id ?? index}`
    steps.set(key, record)
  })
  return [...steps.values()]
}

export function workflowGroups(records: RunRecord[]): WorkflowGroup[] {
  const roots = records.filter(record => record.kind === 'workflow' && record.plugin_id && record.workflow_name && record.operation_id)
  return roots.reduce<WorkflowGroup[]>((groups, root) => {
    const operationId = root.operation_id!
    let group = groups.find(candidate => candidate.operationId === operationId)
    if (!group) {
      group = { operationId, title: root.workflow_name!, records: [] }
      groups.push(group)
    }
    group.records.push(root)
    return groups
  }, []).map(group => ({
    ...group,
    records: records.filter(record =>
      (record.kind === 'workflow' && record.operation_id === group.operationId) || record.parent_ids?.includes(group.operationId)
    )
  }))
}

export function WorkflowRuns({ records, runs = [], activeRun, paused = true }: { records: RunRecord[]; runs?: Snapshot['runs']; activeRun?: Snapshot['active_run']; paused?: boolean }) {
  const persisted = runs.flatMap(run => run.records ?? [])
  const active = activeRun?.records ?? []
  const runStatusByRecord = new Map<string, string>()
  runs.forEach(run => run.records?.forEach(record => {
    if (run.status) runStatusByRecord.set(recordKey(record), run.status)
  }))
  if (paused) active.forEach(record => runStatusByRecord.set(recordKey(record), 'paused'))
  const all = [...new Map([...persisted, ...active, ...records].map(record => [recordKey(record), record])).values()]
  return <div className="workflow-runs">
    {workflowGroups(all).map(group => {
      const roots = group.records.filter(record => record.kind === 'workflow')
      const terminal = [...roots].reverse().find(record => ['complete', 'completed', 'success', 'failed', 'cancelled', 'canceled'].includes(record.status ?? ''))
      const fallbackStatus = group.records.map(record => runStatusByRecord.get(recordKey(record))).find(Boolean)
      const status = terminal?.status ?? fallbackStatus ?? roots.at(-1)?.status ?? 'running'
      return <WorkflowCard key={group.operationId} group={group} status={status} />
    })}
  </div>
}

function recordKey(record: RunRecord): string {
  return `${record.operation_id ?? ''}:${record.seq ?? record.node ?? record.kind}:${record.kind}`
}

function WorkflowCard({ group, status }: { group: WorkflowGroup; status: string }) {
  const [open, setOpen] = useState(false)
  const icon = open ? <ChevronDown size={15}/> : <ChevronRight size={15}/>
  const steps = workflowSteps(group.records)
  return <section className="workflow-card" data-operation-id={group.operationId}>
    <button className="workflow-card-heading" aria-expanded={open} onClick={() => setOpen(!open)}>{icon}<strong>{group.title}</strong><span>{statusLabel(status)}</span></button>
    {open && <ol>
      {steps.map((step, index) => step.kind === 'workflow.node' ? <li key={step.operation_id ?? step.seq ?? index}>
        <span>{step.node ?? '工作流步骤'}</span><span>{String(step.summary ?? statusLabel(stepStatus(step, status)))}</span>
        {step.summary && stepStatus(step, status) !== step.status && <small>{statusLabel(stepStatus(step, status))}</small>}
      </li> : <li key={step.operation_id ?? step.call_id ?? index}>
        <span>工具：{step.name ?? step.call_id ?? '工具'}</span><span>{statusLabel(stepStatus(step, status))}</span>
        {outputSummary(step.output) && <small>{outputSummary(step.output)}</small>}
      </li>)}
    </ol>}
  </section>
}

export function stepStatus(step: RunRecord, workflowStatus: string): string {
  const currentStatus = step.status ?? (step.kind === 'tool_start' ? 'running' : step.kind)
  if (['failed', 'cancelled', 'canceled'].includes(workflowStatus) && currentStatus === 'running') return workflowStatus
  return currentStatus
}

function outputSummary(output: RunRecord['output']): string {
  if (typeof output === 'string') return output
  if (output && typeof output === 'object' && 'summary' in output && typeof output.summary === 'string') return output.summary
  return ''
}

function statusLabel(status: string): string {
  return ({ running: '运行中', paused: '已暂停', complete: '已完成', completed: '已完成', success: '已完成', failed: '失败', cancelled: '已取消', canceled: '已取消', tool_start: '运行中', tool_end: '已完成', tool_error: '失败' } as Record<string, string>)[status] ?? status
}
