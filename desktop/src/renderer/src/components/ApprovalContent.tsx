/**
 * @file ApprovalContent.tsx
 * @description 工具调用审批与敏感操作内容结构化呈现组件。
 * 负责解析智能体提出的工具操作申请（如文件读写、命令执行、外部服务调用），
 * 将参数载荷及嵌套 JSON 解析为人类易读的标签与详情视图供用户审查确认。
 * @author tongliga
 * @date 2026-10-16
 */

import { Fragment } from 'react'
import type { Json, Question } from '../../../shared/protocol'

/**
 * 工具操作参数字段名至人类友好中文标签的映射表。
 */
const labels: Record<string, string> = {
  description: '任务说明',
  task: '任务',
  task_type: '任务类型',
  analysis_type: '分析方式',
  analysis_goal: '分析目标',
  file_path: '文件路径',
  path: '路径',
  columns: '分析字段',
  subagent_type: '执行助手',
  workflow_name: '工作流程',
  input: '输入内容',
  output: '输出内容',
  command: '执行命令',
  content: '文件内容',
  url: '访问地址',
  query: '查询内容'
}

/**
 * 常用敏感动作名至中文动作描述的映射表。
 */
const actions: Record<string, string> = {
  task: '执行任务',
  write_file: '写入文件',
  read_file: '读取文件',
  edit_file: '修改文件',
  shell: '执行命令',
  execute: '执行命令'
}

/**
 * 结构化展示任意 JSON 值的递归渲染组件。
 * 自动识别 null、布尔、数组、键值字典对象，并对包含内嵌 JSON 字符串的文本进行智能二次解码。
 *
 * @param props - 包含待格式化展示的 JSON 值
 */
export function ReadableValue({ value }: { value: Json }) {
  if (value === null) return <span className="approval-empty">未指定</span>
  if (typeof value === 'boolean') return <span>{value ? '是' : '否'}</span>
  if (Array.isArray(value)) {
    return value.length ? (
      <ul className="approval-values">
        {value.map((item, index) => (
          <li key={index}><ReadableValue value={item} /></li>
        ))}
      </ul>
    ) : (
      <span className="approval-empty">无</span>
    )
  }
  if (typeof value === 'object') {
    return Object.keys(value).length ? (
      <dl className="approval-fields">
        {Object.entries(value).map(([key, item]) => (
          <Fragment key={key}>
            <dt>{labels[key] ?? key.replaceAll('_', ' ')}</dt>
            <dd>
              {(key === 'command' || key === 'content') && typeof item === 'string'
                ? <span className="approval-text">{item}</span>
                : <ReadableValue value={item} />}
            </dd>
          </Fragment>
        ))}
      </dl>
    ) : (
      <span className="approval-empty">无</span>
    )
  }
  if (typeof value === 'string') {
    // 工具描述中可能在普通自然语言正文中嵌入了第二层 JSON 字符串，尝试提取并解码
    const marker = /(?:^\s*|输入\s*JSON\s*[:：]\s*)(?=[\[{])/i.exec(value)
    if (marker) {
      const start = marker.index + marker[0].length
      const endings = value.slice(start).matchAll(/[\]}]/g)
      for (const ending of endings) {
        const end = start + ending.index! + 1
        let parsed: Json
        try {
          parsed = JSON.parse(value.slice(start, end)) as Json
        } catch {
          continue
        }
        return (
          <>
            <span className="approval-text">
              {value.slice(0, start).replace(/输入\s*JSON\s*[:：]?\s*$/i, '输入信息：\n')}
            </span>
            <ReadableValue value={parsed} />
            {value.slice(end).trim() && <ReadableValue value={value.slice(end)} />}
          </>
        )
      }
    }
    return <span className="approval-text">{value}</span>
  }
  return <span>{value}</span>
}

/**
 * 智能体工具操作申请详情展示组件。
 *
 * 第一步：遍历 action_requests 中的每一个操作指令。
 * 第二步：提取说明文本 description，针对长文本提供折叠预览与完整展开功能。
 * 第三步：提取具体业务参数字段并使用 ReadableValue 进行可视化排版。
 *
 * @param props - 包含待审核操作请求列表的对象
 */
export function ApprovalContent({ requests }: { requests: NonNullable<Question['action_requests']> }) {
  return (
    <div className="approval-operations">
      {requests.map((request, index) => {
        const args = request.args
        const description = args && !Array.isArray(args) && typeof args === 'object' && typeof args.description === 'string'
          ? args.description
          : ''
        const long = description.length > 180 || /[\[{]/.test(description)
        const summary = description.split(/[\n\[{]|输入\s*JSON\s*[:：]/i)[0]
        const fields = description && args && typeof args === 'object' && !Array.isArray(args)
          ? Object.fromEntries(Object.entries(args).filter(([key]) => key !== 'description'))
          : args

        return (
          <article className="approval-operation" key={index}>
            <div className="approval-operation-heading">
              <span className="approval-step">{index + 1}</span>
              <strong>{actions[request.name] ?? request.name.replaceAll('_', ' ')}</strong>
            </div>
            {description && (
              <div className="approval-description">
                {long ? (
                  <>
                    <div className="approval-preview">{summary || '查看任务说明及输入信息'}</div>
                    <details className="decision-details">
                      <summary>查看完整任务说明</summary>
                      <ReadableValue value={description} />
                    </details>
                  </>
                ) : (
                  <ReadableValue value={description} />
                )}
              </div>
            )}
            {fields && typeof fields === 'object' && Object.keys(fields).length === 0 ? null : (
              <ReadableValue value={fields} />
            )}
          </article>
        )
      })}
    </div>
  )
}


