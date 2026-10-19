/**
 * @file ToolCard.tsx
 * @description 单项工具执行卡片组件。
 * 负责呈现模型调用的工具名称、状态（成功/失败/运行中）、调用 ID、执行耗时、
 * 传入参数 JSON 及返回结果，并对参数和结果中的文件路径提供一键点击直达。
 * @author tongliga
 * @date 2026-10-19
 */

import React, { useState } from 'react'
import {
  Check,
  Copy,
  Terminal,
  FileCode,
  Search,
  Wrench,
  CheckCircle2,
  AlertCircle,
  Loader2
} from 'lucide-react'
import type { RunRecord } from '../../../shared/protocol'
import { FileLink, isFilePath } from './FileLink'

/**
 * 工具调用卡片组件属性接口。
 */
type ToolCardProps = {
  /** 工具调用唯一标识符 */
  callId: string
  /** 工具执行记录快照 */
  record: RunRecord
}

/**
 * 提取对象或字符串中的所有文件或目录路径。
 */
function extractPaths(val: unknown): string[] {
  const result: string[] = []
  if (typeof val === 'string' && isFilePath(val)) {
    result.push(val)
  } else if (typeof val === 'object' && val !== null) {
    const scan = (item: Record<string, unknown>) => {
      for (const v of Object.values(item)) {
        if (typeof v === 'string' && isFilePath(v)) {
          result.push(v)
        } else if (v && typeof v === 'object' && !Array.isArray(v)) {
          scan(v as Record<string, unknown>)
        }
      }
    }
    scan(val as Record<string, unknown>)
  }
  return [...new Set(result)]
}

/**
 * 根据工具名返回识别图标。
 */
function getToolIcon(name?: string) {
  const n = (name ?? '').toLowerCase()
  if (n.includes('repl') || n.includes('exec') || n.includes('shell') || n.includes('cmd')) return <Terminal size={14} />
  if (n.includes('file') || n.includes('read') || n.includes('write')) return <FileCode size={14} />
  if (n.includes('search') || n.includes('find') || n.includes('grep')) return <Search size={14} />
  return <Wrench size={14} />
}

/**
 * 工具执行细节折叠卡片组件。
 *
 * 第一步：解析工具执行记录。
 * 第二步：渲染可展开折叠卡片，包含工具名、状态徽章、耗时、输入参数与结构化输出。
 * 第三步：自动提取参数与结果中的文件路径生成可点击直达的胶囊链接。
 *
 * @param props - 组件属性
 */
export function ToolCard({ callId, record }: ToolCardProps) {
  const output = record.output
  const [copiedArg, setCopiedArg] = useState(false)
  const [copiedRes, setCopiedRes] = useState(false)

  const argPaths = extractPaths(record.arguments)
  const outPaths = extractPaths(output)

  const isSuccess = record.status === 'success' || record.kind === 'tool_end'
  const isFailed = record.status === 'failed' || record.kind === 'tool_error'
  const isRunning = record.status === 'running' || record.kind === 'tool_start'

  return (
    <details className={`tool-card modern-tool-card ${isRunning ? 'is-running' : isFailed ? 'is-failed' : 'is-success'}`}>
      <summary>
        <span className="tool-summary-title">
          <span className="tool-type-icon">{getToolIcon(record.name)}</span>
          <strong>{record.name}</strong>
        </span>
        <small className="tool-summary-meta">
          <span className="tool-status-badge">
            {isRunning ? (
              <Loader2 size={11} className="act-spinner" />
            ) : isFailed ? (
              <AlertCircle size={11} className="act-status-err" />
            ) : (
              <CheckCircle2 size={11} className="act-status-ok" />
            )}
            {record.status}
          </span>
          <span className="tool-sep">·</span>
          <span className="tool-call-id">{callId}</span>
        </small>
      </summary>
      <div className="tool-detail">
        {record.elapsed != null && (
          <p className="tool-elapsed-line">耗时：{(record.elapsed as number).toFixed(2)} 秒</p>
        )}

        <div className="tool-section-head">
          <b>参数</b>
          <button
            type="button"
            className="tool-copy-json"
            onClick={e => {
              e.stopPropagation()
              void navigator.clipboard.writeText(JSON.stringify(record.arguments, null, 2))
              setCopiedArg(true)
              setTimeout(() => setCopiedArg(false), 1600)
            }}
          >
            {copiedArg ? <Check size={11} /> : <Copy size={11} />}
            <span>{copiedArg ? '已复制' : '复制'}</span>
          </button>
        </div>
        {argPaths.length > 0 && (
          <div className="tool-linked-files">
            <span className="tool-linked-label">直接跳转：</span>
            {argPaths.map((p, i) => (
              <FileLink key={i} path={p} />
            ))}
          </div>
        )}
        <pre>{JSON.stringify(record.arguments, null, 2)}</pre>

        <div className="tool-section-head">
          <b>结果</b>
          <button
            type="button"
            className="tool-copy-json"
            onClick={e => {
              e.stopPropagation()
              void navigator.clipboard.writeText(JSON.stringify(output, null, 2))
              setCopiedRes(true)
              setTimeout(() => setCopiedRes(false), 1600)
            }}
          >
            {copiedRes ? <Check size={11} /> : <Copy size={11} />}
            <span>{copiedRes ? '已复制' : '复制'}</span>
          </button>
        </div>
        {outPaths.length > 0 && (
          <div className="tool-linked-files">
            <span className="tool-linked-label">直接跳转：</span>
            {outPaths.map((p, i) => (
              <FileLink key={i} path={p} />
            ))}
          </div>
        )}
        <pre>{JSON.stringify(output, null, 2)}</pre>
      </div>
    </details>
  )
}
