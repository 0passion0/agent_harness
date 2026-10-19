import { Fragment, useId, useState } from 'react'
import { ArrowRight, Check, LockKeyhole, MessageCircle, Puzzle, Shield } from 'lucide-react'
import type { Json } from '../../../shared/protocol'
import './Decision.css'
import { ApprovalContent, ReadableValue } from './ApprovalContent'
import type { Question } from '../../../shared/protocol'

export function Decision({ kind, question, actionRequests, options = [], pluginId, pluginName, purpose, inputScope, plannedOutputs, allowedEffects, onAnswer, onStop }: {
  kind: string
  question: string
  actionRequests?: Question['action_requests']
  options?: string[]
  pluginId?: string
  pluginName?: string
  purpose?: string
  inputScope?: Json
  plannedOutputs?: Json
  allowedEffects?: Json
  onAnswer(value: string): void | Promise<void>
  onStop(): void
}) {
  const [answer, setAnswer] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const titleId = useId()
  const plugin = kind === 'plugin_authorization'
  const approval = kind === 'approval' || plugin
  const hasOptions = !approval && options.length > 0
  const response = hasOptions && selected !== null && selected < options.length ? options[selected] : answer.trim()
  const effects: Record<string, string> = { read: '读取文件', write: '创建或修改文件', external: '访问外部服务' }
  const scopes = [
    ['本次输入', inputScope], ['计划产出', plannedOutputs],
    ['本次操作', Array.isArray(allowedEffects) ? allowedEffects.map(value => effects[String(value)] ?? value) : allowedEffects]
  ].filter(([, value]) => value != null && (!Array.isArray(value) || value.length > 0)) as Array<[string, Json]>
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
