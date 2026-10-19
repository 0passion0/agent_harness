/**
 * @file ResearchQuestion.tsx
 * @description 科研问题补充与研究假设确认表单组件。
 * 当数据分析智能体需要用户针对特定科研问题（如样本筛选基线、对照组定义）提供补充信息时内嵌展示。
 * @author tongliga
 * @date 2026-10-16
 */

import { useState } from 'react'
import type { Question } from '../../../../shared/protocol'

/**
 * 科研问题表单组件属性接口。
 */
type ResearchQuestionProps = {
  /** 交互提问对象 */
  question: Question
  /** 用户提交答复后的回调函数 */
  onAnswer(answer: string): void
  /** 表单是否禁用（如正在提交中） */
  disabled: boolean
}

/**
 * 科研问题回答输入表单组件。
 *
 * @param props - 组件属性
 */
export function ResearchQuestion({ question, onAnswer, disabled }: ResearchQuestionProps) {
  const [answer, setAnswer] = useState('')

  return <form className="research-question" onSubmit={event => {
    event.preventDefault()
    onAnswer(answer)
  }}>
    <label htmlFor="research-answer">研究问题：{question.question}</label>
    <textarea
      id="research-answer"
      value={answer}
      disabled={disabled}
      onChange={event => setAnswer(event.target.value)}
      rows={3}
    />
    <button disabled={disabled || !answer.trim()}>提交回答</button>
  </form>
}
