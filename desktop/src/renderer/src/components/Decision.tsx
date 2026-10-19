/**
 * @file Decision.tsx
 * @description 用户决策交互卡片组件。
 * 负责在 Agent 运行过程中需要用户介入时，呈现不同类型的决策界面：
 * 1. 插件首次授权（plugin_authorization）：展示插件信息、作用范围并提供持久化授权确认；
 * 2. 危险操作授权（approval）：展示待执行操作详情并提供允许/拒绝选项；
 * 3. 单选或文本补充问答（question）：提供选项单选及自定义文本输入回复。
 * @author tongliga
 * @date 2026-10-16
 */

import { Fragment, useId, useState } from 'react'
import { ArrowRight, Check, LockKeyhole, MessageCircle, Puzzle, Shield } from 'lucide-react'
import type { Json, Question } from '../../../shared/protocol'
import './Decision.css'
import { ApprovalContent, ReadableValue } from './ApprovalContent'

/**
 * 决策组件属性接口。
 */
export interface DecisionProps {
  /** 决策交互类型（'plugin_authorization' | 'approval' | 'question' 等） */
  kind: string
  /** 提示问题或授权标题文本 */
  question: string
  /** 待审批的具体操作请求列表 */
  actionRequests?: Question['action_requests']
  /** 候选单选选项列表（适用于选择题模式） */
  options?: string[]
  /** 待授权插件的唯一标识符 */
  pluginId?: string
  /** 待授权插件的展示名称 */
  pluginName?: string
  /** 插件调用目的或任务说明 */
  purpose?: string
  /** 本次任务涉及的输入范围 */
  inputScope?: Json
  /** 本次任务预计生成的文件或产出 */
  plannedOutputs?: Json
  /** 允许的作用域/副作用类别（例如 read、write、external） */
  allowedEffects?: Json
  /** 用户提交答复或确认/拒绝决策时的回调函数 */
  onAnswer(value: string): void | Promise<void>
  /** 用户点击取消任务时的回调函数 */
  onStop(): void
}

/**
 * 用户交互决策卡片组件。
 *
 * 第一步：根据 kind 判断当前决策形态（插件授权、普通操作授权或提问回答）。
 * 第二步：规范化输入作用域、副作用等详情字段，以便展示审查明细。
 * 第三步：渲染卡片头部（图标、标题与状态标识）。
 * 第四步：根据形态渲染主体表单（插件详情折叠块、操作审批项或单选/自定义输入框）。
 * 第五步：提供防止重复提交的安全提交逻辑与取消任务入口。
 *
 * @param props - 决策组件属性
 */
export function Decision({
  kind,
  question,
  actionRequests,
  options = [],
  pluginId,
  pluginName,
  purpose,
  inputScope,
  plannedOutputs,
  allowedEffects,
  onAnswer,
  onStop
}: DecisionProps) {
  const [answer, setAnswer] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const titleId = useId()

  // 判定当前决策的形态分类
  const plugin = kind === 'plugin_authorization'
  const approval = kind === 'approval' || plugin
  const hasOptions = !approval && options.length > 0
  const response = hasOptions && selected !== null && selected < options.length ? options[selected] : answer.trim()

  // 副作用标识映射为通俗易懂的中文说明
  const effects: Record<string, string> = { read: '读取文件', write: '创建或修改文件', external: '访问外部服务' }
  const scopes = [
    ['本次输入', inputScope],
    ['计划产出', plannedOutputs],
    ['本次操作', Array.isArray(allowedEffects) ? allowedEffects.map(value => effects[String(value)] ?? value) : allowedEffects]
  ].filter(([, value]) => value != null && (!Array.isArray(value) || value.length > 0)) as Array<[string, Json]>

  /**
   * 提交当前决策结果。
   * 防御性防止重复点击，执行完成后无论成功与否均重置 submitting 状态。
   *
   * @param value - 提交的操作值（如 'approve'、'reject' 或文本选项）
   */
  async function submit(value: string) {
    if (submitting) return
    setSubmitting(true)
    try { await onAnswer(value) } finally { setSubmitting(false) }
  }

  return <section className={`decision${plugin ? ' plugin-decision' : ''}`} role="region" aria-labelledby={titleId} aria-busy={submitting}>
    <div className="decision-heading">
      <div className="decision-icon">{plugin ? <Puzzle size={20} /> : approval ? <Shield size={20} /> : <MessageCircle size={20} />}</div>
      <div className="decision-title">
        <strong id={titleId}>{plugin ? '允许使用此插件？' : approval ? '需要你的授权' : hasOptions ? '请选择下一步' : '需要补充信息'}</strong>
        {plugin && <span>首次使用确认</span>}
      </div>
      <span className="decision-status" role="status">等待确认</span>
    </div>
    {plugin ? <>
      <div className="decision-plugin"><strong>{pluginName || pluginId || '插件'}</strong>{pluginId && pluginName && pluginId !== pluginName && <code>{pluginId}</code>}</div>
      <p className="decision-summary">{purpose || '使用插件完成当前任务。'}</p>
      <div className="decision-trust"><LockKeyhole size={15} /><span>授权后，此插件可在后续任务中使用，无需重复确认。授权仅保存在当前账号，系统权限限制仍然生效。</span></div>
      {scopes.length > 0 && <details className="decision-details"><summary>查看本次任务详情</summary><dl className="approval-scope">
        {scopes.map(([label, value]) => <Fragment key={label}><dt>{label}</dt><dd><ReadableValue value={value} /></dd></Fragment>)}
      </dl></details>}
    </> : approval ? <>
      <p className="decision-intro">{actionRequests?.length ? '请确认以下操作，允许后将继续执行。' : question}</p>
      {!!actionRequests?.length && <ApprovalContent requests={actionRequests} />}
    </> : <p>{question}</p>}
    {approval ? <div className="decision-actions">
      <button disabled={submitting} onClick={() => void submit('reject')}>{plugin ? '暂不授权' : '拒绝操作'}</button>
      <button className="primary-action" disabled={submitting} onClick={() => void submit('approve')}><Check size={15} />{submitting ? '正在提交…' : plugin ? '授权并记住' : '允许这次操作'}</button>
    </div> : <form onSubmit={event => { event.preventDefault(); if (response) void submit(response) }}>
      {hasOptions && <fieldset className="decision-options" disabled={submitting} aria-label="可选回答">
        {[...options, '自定义回答'].map((option, index) => <label className={`decision-option${selected === index ? ' selected' : ''}`} key={index}>
          <input type="radio" name={titleId} checked={selected === index} onChange={() => { setSelected(index); setAnswer('') }} />
          <span>{option}</span>
        </label>)}
      </fieldset>}
      {(!hasOptions || selected === options.length) && <textarea aria-label={hasOptions ? '自定义回答' : '补充信息'} placeholder={hasOptions ? '输入你的想法…' : '输入答复，帮助任务继续…'} rows={2} value={answer} onChange={event => setAnswer(event.target.value)} disabled={submitting} />}
      <div className="decision-actions">
        <button type="button" onClick={onStop} disabled={submitting}>取消任务</button>
        <button className="primary-action" disabled={submitting || !response} type="submit">{submitting ? '正在提交…' : hasOptions ? '确认选择' : '提交答复'} <ArrowRight size={15} /></button>
      </div>
    </form>}
    {!plugin && <small>未确认前不会继续执行当前步骤。</small>}
  </section>
}
