import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Decision } from '../../src/renderer/src/components/Decision'

const callbacks = { onAnswer: () => {}, onStop: () => {} }

test('questions show concise choices, an unselected custom answer and an explicit submit', () => {
  const html = renderToStaticMarkup(<Decision {...callbacks} kind="question" question="选择报告格式" options={['简要摘要', '完整报告']} />)
  expect(html).toContain('选择报告格式')
  expect(html.match(/type="radio"/g)).toHaveLength(3)
  expect(html).toContain('自定义回答')
  expect(html).toContain('确认选择')
  expect(html).not.toContain('checked=""')
  expect(html).not.toContain('授权并记住')
})

test('free text questions and ordinary approvals retain their own actions', () => {
  const question = renderToStaticMarkup(<Decision {...callbacks} kind="question" question="补充研究目标" />)
  expect(question).toContain('aria-label="补充信息"')
  expect(question).toContain('提交答复')
  const approval = renderToStaticMarkup(<Decision {...callbacks} kind="approval" question="写入 report.md" />)
  expect(approval).toContain('允许这次操作')
  expect(approval).not.toContain('无需重复确认')
})

test('tool approvals display the exact native tool name and arguments', () => {
  const html = renderToStaticMarkup(<Decision {...callbacks} kind="approval" question="确认操作" actionRequests={[{ name: 'write_file', args: { file_path: '/report.md', content: 'verified' } }]} />)
  expect(html).toContain('write_file')
  expect(html).toContain('/report.md')
  expect(html).toContain('verified')
  expect(html).toContain('允许这次操作')
})
