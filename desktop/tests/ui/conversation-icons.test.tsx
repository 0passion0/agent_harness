import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Conversation } from '../../src/renderer/src/components/Conversation'
import { initialState } from '../../src/renderer/src/state/agent-state'

test('uses the MedPeer asset for saved and streaming assistant avatars', () => {
  const html = renderToStaticMarkup(<Conversation openArtifact={() => {}} state={{ ...initialState, stream: '正在回复',
    snapshot: { session_id: 's', thread_id: 't', thread_ids: ['t'], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false,
      messages: [{ type: 'human', data: { content: '问题' } }, { type: 'ai', data: { content: '答复' } }] }
  }} />)
  expect(html.match(/class="message-avatar"/g)).toHaveLength(2)
  expect(html.match(/src="[^\"]*medpeer\.ico[^\"]*"/g)).toHaveLength(2)
  expect(html).not.toContain('✦')
})
