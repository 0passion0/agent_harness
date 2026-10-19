/**
 * @file InlineActivity.tsx
 * @description 智能体对话流内联实时活动展示组件。
 * 在会话流中优雅、精美地呈现后台 LLM 输出轨迹、工具调用（tool calls）以及插件调用（plugin/workflow invocations）。
 * 遵循极简现代美学，默认紧凑可折叠，动态展示运行耗时、状态图标、入参与出参，并让其中的文件路径支持一键点击穿透。
 * @author tongliga
 * @date 2026-10-19
 */

import React, { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Cpu,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Terminal,
  FileCode,
  Search,
  Wrench,
  Boxes,
  Copy,
  Check,
  Sparkles
} from 'lucide-react'
import type { RunRecord } from '../../../shared/protocol'
import { FileLink, isFilePath } from './FileLink'
import { WorkflowRuns, stepStatus, workflowSteps, workflowGroups } from './WorkflowRuns'

/**
 * 根据工具名或类别返回最具辨识度的现代图标。
 */
function getToolIcon(name?: string) {
  const n = (name ?? '').toLowerCase()
  if (n.includes('repl') || n.includes('exec') || n.includes('shell') || n.includes('cmd') || n.includes('terminal')) {
    return <Terminal size={13} className="act-icon-terminal" />
  }
  if (n.includes('read') || n.includes('write') || n.includes('file') || n.includes('cat')) {
    return <FileCode size={13} className="act-icon-file" />
  }
  if (n.includes('search') || n.includes('find') || n.includes('grep') || n.includes('query')) {
    return <Search size={13} className="act-icon-search" />
  }
  if (n.includes('workflow') || n.includes('flow') || n.includes('pipeline')) {
    return <Boxes size={13} className="act-icon-workflow" />
  }
  if (n.includes('model') || n.includes('llm') || n.includes('reason') || n.includes('think')) {
    return <Sparkles size={13} className="act-icon-ai" />
  }
  return <Wrench size={13} className="act-icon-tool" />
}

/**
 * 解析并美化展示工具输入或输出载荷。若检测到文件路径则内嵌交互式 FileLink。
 */
function FormattedPayload({ data }: { data: unknown }) {
  const [copied, setCopied] = useState(false)
  if (data === undefined || data === null) {
    return <span className="act-empty-text">无载荷</span>
  }

  const jsonStr = typeof data === 'string' ? data : JSON.stringify(data, null, 2)

  // 提取载荷中可能包含的文件路径
  const filePaths: string[] = []
  if (typeof data === 'object' && data !== null) {
    const scan = (obj: Record<string, unknown>) => {
      for (const [key, val] of Object.entries(obj)) {
        if (typeof val === 'string' && isFilePath(val)) {
          filePaths.push(val)
        } else if (val && typeof val === 'object' && !Array.isArray(val)) {
          scan(val as Record<string, unknown>)
        }
      }
    }
    scan(data as Record<string, unknown>)
  } else if (typeof data === 'string' && isFilePath(data)) {
    filePaths.push(data)
  }

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    void navigator.clipboard.writeText(jsonStr)
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }

  return (
    <div className="act-payload-box">
      {filePaths.length > 0 && (
        <div className="act-file-chips">
          <span className="act-file-chips-label">涉及文件：</span>
          {filePaths.map((fp, i) => (
            <FileLink key={i} path={fp} />
          ))}
        </div>
      )}
      <div className="act-payload-content">
        <button
          type="button"
          className="act-copy-btn"
          title={copied ? '已复制' : '复制内容'}
          onClick={handleCopy}
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
          <span>{copied ? '已复制' : '复制'}</span>
        </button>
        <pre className="act-json-pre">{jsonStr}</pre>
      </div>
    </div>
  )
}

/**
 * 单条工具或步骤活动卡片。
 */
function ActivityItem({ record, status }: { record: RunRecord; status: string }) {
  const [open, setOpen] = useState(false)
  const isRunning = status === 'running'
  const isError = status === 'failed' || status === 'tool_error'
  const isComplete = ['success', 'complete', 'completed', 'tool_end'].includes(status)

  const name = record.name || record.workflow_name || record.node || record.kind
  const elapsed = typeof record.elapsed === 'number' ? `${record.elapsed.toFixed(2)}s` : ''

  return (
    <details className={`act-item ${isRunning ? 'is-running' : isError ? 'is-error' : isComplete ? 'is-complete' : 'is-interrupted'}`}
      onToggle={event => setOpen(event.currentTarget.open)}>
      <summary className="act-item-header">
        <span className="act-item-icon-wrap">
          {isRunning ? (
            <Loader2 size={13} className="act-spinner" />
          ) : isError ? (
            <AlertCircle size={13} className="act-status-err" />
          ) : isComplete ? (
            <CheckCircle2 size={13} className="act-status-ok" />
          ) : <Cpu size={13} />}
        </span>

        <span className="act-item-title-wrap">
          {getToolIcon(name)}
          <strong className="act-item-title">{name}</strong>
          {record.plugin_id && <span className="act-plugin-badge">{record.plugin_id}</span>}
        </span>

        <span className="act-item-meta">
          {elapsed && <span className="act-elapsed">{elapsed}</span>}
          <span className="act-status-tag">
            {isRunning ? '执行中' : isError ? '失败' : isComplete ? '完成' : status === 'paused' ? '等待答复' : status === 'cancelled' || status === 'canceled' ? '已取消' : '未完成'}
          </span>
          <span className="act-toggle-arrow">
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </span>
        </span>
      </summary>

      {open && (
        <div className="act-item-details">
          {record.summary && <div className="act-summary-line">{String(record.summary)}</div>}
          {record.arguments !== undefined && (
            <div className="act-detail-section">
              <span className="act-detail-label">调用参数</span>
              <FormattedPayload data={record.arguments} />
            </div>
          )}
          {record.output !== undefined && (
            <div className="act-detail-section">
              <span className="act-detail-label">执行结果</span>
              <FormattedPayload data={record.output} />
            </div>
          )}
        </div>
      )}
    </details>
  )
}

export interface InlineActivityProps {
  /** 运行记录列表（包含 tool_start, tool_end, workflow.node, model_start 等） */
  records?: RunRecord[]
  /** 当前正在执行中的活跃工具字典 */
  tools?: Record<string, RunRecord>
  /** 是否正在执行中 */
  running?: boolean
  status?: string
}

/**
 * 现代极简风格的内联活动执行流组件。
 * 聚合展示 LLM 后台动作、工具调用、插件执行细节。
 */
export function InlineActivity({ records = [], tools = {}, running = false, status = running ? 'running' : 'complete' }: InlineActivityProps) {
  const toolRecords = workflowSteps([...records, ...Object.entries(tools).map(([id, record]) => ({ ...record, call_id: id }))])
  const statuses = toolRecords.map(record => stepStatus(record, status))
  const activeCount = statuses.filter(value => value === 'running').length
  const completedCount = statuses.filter(value => ['success', 'complete', 'completed', 'tool_end'].includes(value)).length
  const failedCount = statuses.filter(value => value === 'failed' || value === 'tool_error').length
  const current = toolRecords.filter((_, index) => statuses[index] === 'running').at(-1)
  const isRunning = status === 'running'
  const grouped = new Set(workflowGroups(records).flatMap(group => workflowSteps(group.records).map(record => record.call_id ?? record.operation_id)).filter((id): id is string => typeof id === 'string'))
  const label = status === 'paused' ? '等待你的答复' : status === 'stopping' ? '正在停止' : status === 'cancelled' ? '执行已取消' : status === 'failed' ? '执行失败' : status === 'disconnected' ? '连接已中断' : isRunning ? (current?.name || current?.node || '正在处理') : '执行记录'

  if (toolRecords.length === 0 && !isRunning && !records.some(record => record.kind === 'workflow')) {
    return null
  }

  return (
    <details className={`inline-activity-card ${isRunning ? 'is-active' : ''}`}>
      <summary className="inline-activity-bar">
        <div className="act-bar-left">
          <span className="act-bar-symbol">
            {isRunning ? <Loader2 size={14} className="act-spinner" /> : status === 'failed' ? <AlertCircle size={14} /> : <Cpu size={14} />}
          </span>
          <span className="act-bar-label">
            {label}
          </span>
          <span className="act-count-pill">
            {toolRecords.length} 项操作
            {activeCount > 0 && <span className="act-active-dot" />}
          </span>
        </div>

        <div className="act-bar-right">
          <span className="act-summary-badge">
            {activeCount ? `${activeCount} 进行中 · ` : ''}{completedCount} 已完成{failedCount ? ` · ${failedCount} 失败` : ''}
          </span>
          <span className="act-chevron">
            <ChevronRight size={14} />
          </span>
        </div>
      </summary>

        <div className="inline-activity-list" role="region" aria-label="执行步骤">
          <WorkflowRuns records={records} status={status} paused={status === 'paused'} />
          {toolRecords.map((item, idx) => (
            grouped.has(item.call_id ?? item.operation_id ?? '') ? null :
              <ActivityItem key={item.call_id ?? item.operation_id ?? idx} record={item} status={statuses[idx]} />
          ))}
          {toolRecords.length === 0 && isRunning && (
            <div className="act-empty-loading">
              <Loader2 size={14} className="act-spinner" />
              <span>正在建立模型推理流与执行计划…</span>
            </div>
          )}
        </div>
    </details>
  )
}
