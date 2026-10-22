import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Conversation } from '../../src/renderer/src/components/Conversation'
import { initialState } from '../../src/renderer/src/state/agent-state'

test('renders saved and streaming assistant text without avatar indentation', () => {
  const html = renderToStaticMarkup(<Conversation openArtifact={() => {}} state={{ ...initialState, stream: '正在回复',
    snapshot: { session_id: 's', thread_id: 't', thread_ids: ['t'], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false,
      messages: [{ type: 'human', data: { content: '问题' } }, { type: 'ai', data: { content: '答复' } }] }
  }} />)
  expect(html).not.toContain('message-avatar')
  expect(html).toContain('codex-conversation')
  expect(html).toContain('答复')
  expect(html).toContain('正在回复')
  expect(html).not.toContain('✦')
})
