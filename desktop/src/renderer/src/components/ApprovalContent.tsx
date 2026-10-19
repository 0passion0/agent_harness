import { Fragment } from 'react'
import type { Json, Question } from '../../../shared/protocol'

const labels: Record<string, string> = {
  description: '任务说明', task: '任务', task_type: '任务类型', analysis_type: '分析方式',
  analysis_goal: '分析目标', file_path: '文件路径', path: '路径', columns: '分析字段',
  subagent_type: '执行助手', workflow_name: '工作流程', input: '输入内容', output: '输出内容',
  command: '执行命令', content: '文件内容', url: '访问地址', query: '查询内容',
}
const actions: Record<string, string> = {
  task: '执行任务', write_file: '写入文件', read_file: '读取文件', edit_file: '修改文件',
  shell: '执行命令', execute: '执行命令',
}

export function ReadableValue({ value }: { value: Json }) {
  if (value === null) return <span className="approval-empty">未指定</span>
  if (typeof value === 'boolean') return <span>{value ? '是' : '否'}</span>
  if (Array.isArray(value)) return value.length
    ? <ul className="approval-values">{value.map((item, index) => <li key={index}><ReadableValue value={item} /></li>)}</ul>
    : <span className="approval-empty">无</span>
  if (typeof value === 'object') return Object.keys(value).length
    ? <dl className="approval-fields">{Object.entries(value).map(([key, item]) => <Fragment key={key}>
      <dt>{labels[key] ?? key.replaceAll('_', ' ')}</dt><dd>{(key === 'command' || key === 'content') && typeof item === 'string'
        ? <span className="approval-text">{item}</span> : <ReadableValue value={item} />}</dd>
    </Fragment>)}</dl>
    : <span className="approval-empty">无</span>
  if (typeof value === 'string') {
    // Tool descriptions may embed a second JSON payload inside ordinary prose.
    const marker = /(?:^\s*|输入\s*JSON\s*[:：]\s*)(?=[\[{])/i.exec(value)
    if (marker) {
      const start = marker.index + marker[0].length
      const endings = value.slice(start).matchAll(/[\]}]/g)
      for (const ending of endings) {
        const end = start + ending.index! + 1
        let parsed: Json
        try { parsed = JSON.parse(value.slice(start, end)) as Json } catch { continue }
        return <><span className="approval-text">{value.slice(0, start).replace(/输入\s*JSON\s*[:：]?\s*$/i, '输入信息：\n')}</span>
          <ReadableValue value={parsed} />
          {value.slice(end).trim() && <ReadableValue value={value.slice(end)} />}</>
      }
    }
    return <span className="approval-text">{value}</span>
  }
  return <span>{value}</span>
}

export function ApprovalContent({ requests }: { requests: NonNullable<Question['action_requests']> }) {
  return <div className="approval-operations">{requests.map((request, index) => {
    const args = request.args
    const description = args && !Array.isArray(args) && typeof args === 'object' && typeof args.description === 'string' ? args.description : ''
    const long = description.length > 180 || /[\[{]/.test(description)
    const summary = description.split(/[\n\[{]|输入\s*JSON\s*[:：]/i)[0]
    const fields = description && args && typeof args === 'object' && !Array.isArray(args)
      ? Object.fromEntries(Object.entries(args).filter(([key]) => key !== 'description')) : args
    return <article className="approval-operation" key={index}>
      <div className="approval-operation-heading"><span className="approval-step">{index + 1}</span><strong>{actions[request.name] ?? request.name.replaceAll('_', ' ')}</strong></div>
      {description && <div className="approval-description">{long ? <>
        <div className="approval-preview">{summary || '查看任务说明及输入信息'}</div>
        <details className="decision-details"><summary>查看完整任务说明</summary><ReadableValue value={description} /></details>
      </> : <ReadableValue value={description} />}</div>}
      {fields && typeof fields === 'object' && Object.keys(fields).length === 0 ? null : <ReadableValue value={fields} />}
    </article>
  })}</div>
}

