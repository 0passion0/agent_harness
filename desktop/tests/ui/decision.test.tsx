import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Decision } from '../../src/renderer/src/components/Decision'

const callbacks = { onAnswer: () => {}, onStop: () => {} }

test('plugin approval explains the durable grant and keeps task details collapsed', () => {
  const html = renderToStaticMarkup(<Decision {...callbacks} kind="plugin_authorization" question="legacy duplicate prompt" pluginId="research.citations" pluginName="引用核查" purpose="核查参考文献" inputScope={['source.csv']} allowedEffects={['read', 'external']} />)
  for (const text of ['引用核查', 'research.citations', '授权并记住', '无需重复确认', 'source.csv', '访问外部服务', '<details']) expect(html).toContain(text)
  expect(html).not.toContain('legacy duplicate prompt')
  expect(html).not.toContain('[&quot;')
})

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

test('tool approvals turn nested JSON and embedded input into readable fields', () => {
  const description = '分析价格与浏览量的关系。\n输入 JSON：\n' + JSON.stringify({ task_type: 'correlation', file_path: '/workspace/销售.csv', columns: ['价格', '浏览量'] }) + '\n注意：排除无效数值。'
  const html = renderToStaticMarkup(<Decision {...callbacks} kind="approval" question="legacy JSON prompt" actionRequests={[
    { name: 'task', args: { description, subagent_type: '数据分析' } },
    { name: 'write_file', args: { path: '/workspace/report.md', content: '<script>unsafe</script>' } },
  ]} />)
  for (const text of ['执行任务', '分析价格与浏览量的关系。', '查看完整任务说明', '文件路径', '/workspace/销售.csv', '分析字段', '浏览量', '排除无效数值', '执行助手', '写入文件', '/workspace/report.md']) expect(html).toContain(text)
  expect(html).not.toContain('legacy JSON prompt')
  expect(html).not.toContain('&quot;task_type&quot;')
  expect(html).not.toContain('输入 JSON')
  expect(html).not.toContain('<script>')
  expect(html).toContain('&lt;script&gt;')
  expect(html).not.toContain('<details open')
})

test('plugin scopes render objects, arrays and false values without JSON serialization', () => {
  const html = renderToStaticMarkup(<Decision {...callbacks} kind="plugin_authorization" question="授权" inputScope={{ file_path: 'source.csv', columns: ['价格'], overwrite: false, optional: null, empty: [] }} />)
  for (const text of ['文件路径', 'source.csv', '价格', '否', '未指定']) expect(html).toContain(text)
  expect(html).not.toContain('&quot;file_path&quot;')
})

test('malformed JSON-like text remains readable without dropping instructions', () => {
  const html = renderToStaticMarkup(<Decision {...callbacks} kind="approval" question="确认" actionRequests={[{ name: 'task', args: { description: '处理 {无效输入} 后保留原文件。' } }]} />)
  expect(html).toContain('处理 {无效输入} 后保留原文件。')
})

test('commands and file content preserve literal JSON and bracketed paths', () => {
  const html = renderToStaticMarkup(<Decision {...callbacks} kind="approval" question="确认" actionRequests={[{ name: 'shell', args: { command: 'echo {"value":1}', content: '{"keep":true}', path: '/workspace/[1]/source.csv' } }]} />)
  for (const text of ['echo {&quot;value&quot;:1}', '{&quot;keep&quot;:true}', '/workspace/[1]/source.csv']) expect(html).toContain(text)
})
