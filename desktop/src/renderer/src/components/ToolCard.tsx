/**
 * @file ToolCard.tsx
 * @description 单项工具执行卡片组件。
 * 负责呈现模型调用的工具名称、状态（成功/失败/运行中）、调用 ID、执行耗时、
 * 传入参数 JSON 及返回结果。
 * @author tongliga
 * @date 2026-10-16
 */

import type { RunRecord } from '../../../shared/protocol'

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
 * 工具执行细节折叠卡片组件。
 *
 * 第一步：解析工具执行记录。
 * 第二步：渲染可展开折叠卡片，包含工具名、状态徽章、耗时、输入参数与结构化输出。
 *
 * @param props - 组件属性
 */
export function ToolCard({ callId, record }: ToolCardProps) {
  const output = record.output

  return (
    <details className="tool-card">
      <summary>
        <span>{record.name}</span>
        <small>{record.status} · {callId}</small>
      </summary>
      <div className="tool-detail">
        {record.elapsed != null && <p>耗时：{(record.elapsed as number).toFixed(2)} 秒</p>}
        <b>参数</b>
        <pre>{JSON.stringify(record.arguments, null, 2)}</pre>
        <b>结果</b>
        <pre>{JSON.stringify(output, null, 2)}</pre>
      </div>
    </details>
  )
}
