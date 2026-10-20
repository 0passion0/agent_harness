import { useId, useState } from 'react'
import type { Json } from '../../../shared/protocol'
import { ApprovalContent } from './ApprovalContent'
import './Decision.css'
export type DecisionProps = {
  kind: string
  question: string
  options?: string[]
  actionRequests?: Array<{ name: string; args: Json }>
  onAnswer(answer: string): void | Promise<void>
  onStop(): void
}
export function Decision({ kind, question, options = [], actionRequests, onAnswer, onStop }: DecisionProps) {
  const title = useId()
  const [answer, setAnswer] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const approval = kind === 'approval' || !!actionRequests?.length
  const response = options.length && selected !== null && selected < options.length ? options[selected] : answer.trim()
  async function submit(value: string) {
    if (submitting) return
    setSubmitting(true)
    try { await onAnswer(value) } finally { setSubmitting(false) }
  }
  return <section className="decision" aria-labelledby={title}>
    <h3 id={title}>{approval ? '需要你的授权' : '需要补充信息'}</h3>
    {!actionRequests?.length && <p>{question}</p>}
    {approval ? <>
      {!!actionRequests?.length && <ApprovalContent requests={actionRequests} />}
      <div className="decision-actions"><button disabled={submitting} onClick={() => void submit('reject')}>拒绝操作</button><button disabled={submitting} onClick={() => void submit('approve')}>允许这次操作</button></div>
    </> : <form onSubmit={e => { e.preventDefault(); if (response) void submit(response) }}>
      {!!options.length && <fieldset disabled={submitting} aria-label="可选回答">{[...options, '自定义回答'].map((option, index) => <label key={index}><input type="radio" name={title} checked={selected === index} onChange={() => setSelected(index)} />{option}</label>)}</fieldset>}
      {(!options.length || selected === options.length) && <textarea aria-label="补充信息" value={answer} onChange={e => setAnswer(e.target.value)} disabled={submitting} />}
      <button type="button" onClick={onStop} disabled={submitting}>取消任务</button><button disabled={submitting || !response}>{options.length ? "确认选择" : "提交答复"}</button>
    </form>}
  </section>
}
