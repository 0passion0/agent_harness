import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ToolCard } from '../../src/renderer/src/components/ToolCard'

test('renders tool arguments, elapsed time, and output as structured JSON', () => {
  const markup = renderToStaticMarkup(<ToolCard callId="call-1" record={{
    kind: 'tool', name: 'read_file', status: 'success', elapsed: 0.12,
    arguments: { path: '/workspace/test.txt' },
    output: { content: 'hello world' }
  }} />)

  expect(markup).toContain('read_file')
  expect(markup).toContain('call-1')
  expect(markup).toContain('/workspace/test.txt')
  expect(markup).toContain('hello world')
  expect(markup).toContain('耗时：0.12 秒')
})
