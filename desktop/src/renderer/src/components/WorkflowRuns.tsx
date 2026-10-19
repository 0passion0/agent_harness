/**
 * @file WorkflowRuns.tsx
 * @description 工作流执行历史与步骤折叠卡片组件。
 * 负责解析会话快照与运行记录（RunRecord）中的工作流事件，将其按 operation_id 分组聚合为工作流卡片，
 * 提取子节点（workflow.node）与关联工具调用（tool_start / tool_end / tool_error），
 * 映射工作流与各个步骤的最终运行状态（运行中、已完成、失败、已暂停、已取消等），
 * 并提供可展开/折叠查看执行步骤明细的交互。
 * @author tongliga
 * @date 2026-10-16
 */

import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { RunRecord, Snapshot } from '../../../shared/protocol'

/**
 * 工作流执行分组。
 */
export type WorkflowGroup = {
  /** 工作流操作唯一标识符（operation_id） */
  operationId: string
  /** 工作流名称或展示标题 */
  title: string
  /** 属于该工作流实例的完整运行记录列表（包括根节点、步骤节点与子工具调用） */
  records: RunRecord[]
}

/**
 * 从一组运行记录中提炼出唯一的执行步骤列表。
 * 去重规则：节点按 operation_id 或 seq 去重；工具调用按 operation_id 或 call_id 去重。
 *
 * @param records - 属于特定工作流的所有运行记录
 * @returns 去重后的步骤节点与工具调用记录数组
 */
export function workflowSteps(records: RunRecord[]): RunRecord[] {
  const steps = new Map<string, RunRecord>()
  records
    .filter(record => record.kind === 'workflow.node' || ['tool_start', 'tool_end', 'tool_error'].includes(record.kind))
    .forEach((record, index) => {
      const key = record.kind === 'workflow.node'
        ? `node:${record.operation_id ?? record.seq ?? index}`
        : `tool:${record.call_id ?? record.operation_id ?? index}`
      steps.set(key, { ...steps.get(key), ...record })
    })
  return [...steps.values()]
}

/**
 * 将平铺的运行记录集合聚类为按工作流实例划分的分组。
 *
 * 第一步：过滤出所有代表工作流根事件的记录（kind 为 'workflow' 且具备 plugin_id、workflow_name、operation_id）。
 * 第二步：按 operationId 建立或合并工作流基础信息（title 与 records 初始根记录）。
 * 第三步：为每个分组归集所有属于该工作流本身的记录以及 parent_ids 中包含该 operationId 的后代事件（如节点与工具调用）。
 *
 * @param records - 原始运行记录数组
 * @returns 聚合后的工作流分组列表
 */
export function workflowGroups(records: RunRecord[]): WorkflowGroup[] {
  // 第一步：筛选工作流根节点记录
  const roots = records.filter(record => record.kind === 'workflow' && record.plugin_id && record.workflow_name && record.operation_id)

  // 第二步：聚合生成初始分组结构
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
    // 第三步：收集属于该分组或子级关联的所有事件记录
    records: records.filter(record =>
      (record.kind === 'workflow' && record.operation_id === group.operationId) || record.parent_ids?.includes(group.operationId)
    )
  }))
}

/**
 * 工作流执行卡片列表组件属性接口。
 */
export interface WorkflowRunsProps {
  /** 当前增量或补充的运行记录列表 */
  records: RunRecord[]
  /** 已持久化的历史运行批次快照列表 */
  runs?: Snapshot['runs']
  /** 当前正在执行中的活跃运行批次快照 */
  activeRun?: Snapshot['active_run']
  /** 当前任务或会话是否处于暂停状态，默认为 true */
  paused?: boolean
  status?: string
}

/**
 * 工作流执行卡片聚合展示组件。
 *
 * 第一步：平铺已持久化的运行记录和活跃运行记录，并根据批次状态（如 paused、failed 等）构建记录状态索引表。
 * 第二步：合并所有记录并按 operation_id/seq/kind 去重。
 * 第三步：调用 workflowGroups 进行层次聚类，并计算每个工作流的最终终端状态（complete/failed/running 等）。
 * 第四步：依次渲染各工作流的折叠卡片组件 WorkflowCard。
 *
 * @param props - 组件属性
 */
export function WorkflowRuns({ records, runs = [], activeRun, paused = true, status: runStatus }: WorkflowRunsProps) {
  // 第一步：整理并索引持久化与活跃批次状态
  const persisted = runs.flatMap(run => run.records ?? [])
  const active = activeRun?.records ?? []
  const runStatusByRecord = new Map<string, string>()

  runs.forEach(run => run.records?.forEach(record => {
    if (run.status) runStatusByRecord.set(recordKey(record), run.status)
  }))

  if (paused) active.forEach(record => runStatusByRecord.set(recordKey(record), 'paused'))

  // 第二步：合并全部记录并按唯一键去重
  const all = [...new Map([...persisted, ...active, ...records].map(record => [recordKey(record), record])).values()]

  // 第三步与第四步：聚类并渲染卡片列表
  return <div className="workflow-runs">
    {workflowGroups(all).map(group => {
      const roots = group.records.filter(record => record.kind === 'workflow')
      const terminal = [...roots].reverse().find(record => ['complete', 'completed', 'success', 'failed', 'cancelled', 'canceled'].includes(record.status ?? ''))
      const fallbackStatus = group.records.map(record => runStatusByRecord.get(recordKey(record))).find(Boolean)
      const status = terminal?.status ?? fallbackStatus ?? runStatus ?? roots.at(-1)?.status ?? 'running'
      return <WorkflowCard key={group.operationId} group={group} status={status} />
    })}
  </div>
}

/**
 * 生成运行记录的唯一标识键，用于跨批次与跨状态合并去重。
 *
 * @param record - 待计算的运行记录
 * @returns 唯一键字符串
 */
function recordKey(record: RunRecord): string {
  return `${record.operation_id ?? ''}:${record.seq ?? record.node ?? record.kind}:${record.kind}`
}

/**
 * 工作流单项折叠卡片组件。
 * 展示工作流标题、状态徽章，支持点击展开/折叠查看具体的子步骤和工具调用详情。
 *
 * @param props - 分组数据与工作流状态
 */
function WorkflowCard({ group, status }: { group: WorkflowGroup; status: string }) {
  const [open, setOpen] = useState(false)
  const icon = open ? <ChevronDown size={15}/> : <ChevronRight size={15}/>
  const steps = workflowSteps(group.records)

  return <section className="workflow-card" data-operation-id={group.operationId}>
    <button className="workflow-card-heading" aria-expanded={open} onClick={() => setOpen(!open)}>
      {icon}<strong>{group.title}</strong><span>{statusLabel(status)}</span>
    </button>
    {open && <ol>
      {steps.map((step, index) => step.kind === 'workflow.node' ? <li key={step.operation_id ?? step.seq ?? index}>
        <span>{step.node ?? '工作流步骤'}</span>
        <span>{String(step.summary ?? statusLabel(stepStatus(step, status)))}</span>
        {step.summary && stepStatus(step, status) !== step.status && <small>{statusLabel(stepStatus(step, status))}</small>}
      </li> : <li key={step.operation_id ?? step.call_id ?? index}>
        <span>工具：{step.name ?? step.call_id ?? '工具'}</span>
        <span>{statusLabel(stepStatus(step, status))}</span>
        {outputSummary(step.output) && <small>{outputSummary(step.output)}</small>}
      </li>)}
    </ol>}
  </section>
}

/**
 * 计算步骤记录在结合其所属工作流上下文后的有效状态。
 * 例如：当工作流整体处于 failed/cancelled 状态且子步骤仍在 running 时，子步骤状态继承终止状态。
 *
 * @param step - 步骤记录对象
 * @param workflowStatus - 所属工作流的整体状态
 * @returns 解析后的步骤状态标识
 */
export function stepStatus(step: RunRecord, workflowStatus: string): string {
  const currentStatus = step.status ?? (step.kind === 'tool_start' ? 'running' : step.kind)
  if (currentStatus === 'running' && workflowStatus !== 'running') {
    return ['complete', 'completed', 'success'].includes(workflowStatus) ? 'interrupted' : workflowStatus
  }
  return currentStatus
}

/**
 * 从步骤或工具的输出中提取可读的文本摘要。
 *
 * @param output - 运行记录中的 output 字段（字符串或包含 summary 的对象）
 * @returns 提取的文本摘要，若无则返回空字符串
 */
function outputSummary(output: RunRecord['output']): string {
  if (typeof output === 'string') return output
  if (output && typeof output === 'object' && 'summary' in output && typeof output.summary === 'string') return output.summary
  return ''
}

/**
 * 将技术状态枚举转换为本地化中文展示标签。
 *
 * @param status - 英文状态标识（如 'running', 'complete', 'failed', 'paused' 等）
 * @returns 中文展示文本
 */
function statusLabel(status: string): string {
  return ({
    running: '运行中',
    paused: '已暂停',
    complete: '已完成',
    completed: '已完成',
    success: '已完成',
    failed: '失败',
    cancelled: '已取消',
    canceled: '已取消',
    tool_start: '运行中',
    tool_end: '已完成',
    tool_error: '失败',
    interrupted: '未完成',
    stopping: '正在停止',
    disconnected: '连接中断'
  } as Record<string, string>)[status] ?? status
}
