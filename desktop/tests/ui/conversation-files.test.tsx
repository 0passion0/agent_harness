import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import { Conversation } from '../../src/renderer/src/components/Conversation'
import { initialState } from '../../src/renderer/src/state/agent-state'
import type { Snapshot } from '../../src/shared/protocol'

const snapshot: Snapshot = {
  session_id: 's', thread_id: 's', thread_ids: ['s'], totals: {}, unsettled_history: false,
  messages: [{ type: 'ai', data: { content: '查看 [新文件](medpeer-artifact:exports/new.pdf) 和 [旧文件](medpeer-artifact:exports/old.pdf)。目录 `desktop/`，函数 `create_agent`，路径 `src/App.tsx`。' } }],
  attachments: [{ id: 'input', workspace_id: 'w', name: '用户上传.pdf', relative_path: 'input.pdf', media_type: 'application/pdf', size: 10 }],
  artifacts: [
    { id: 'old', workspace_id: 'w', name: '旧交付.pdf', relative_path: 'exports/old.pdf', media_type: 'application/pdf', source_run_id: 'old' },
    { id: 'new', workspace_id: 'w', name: '新交付.pdf', relative_path: 'exports/new.pdf', media_type: 'application/pdf', source_run_id: 'new' }
  ],
  runs: [
    { id: 'old', elapsed: 1, tools: {}, requests: [] },
    { id: 'new', elapsed: 1, tools: {}, requests: [] }
  ]
}

test('only latest deliveries get rows while explicit links in history remain usable', () => {
  const html = renderToStaticMarkup(<Conversation state={{ ...initialState, snapshot }} openArtifact={() => {}} />)
  expect(html).toContain('新交付.pdf')
  expect(html).not.toContain('旧交付.pdf')
  expect(html).not.toContain('用户上传.pdf')
  expect(html).toContain('href="medpeer-artifact:exports/new.pdf"')
  expect(html).toContain('href="medpeer-artifact:exports/old.pdf"')
  expect(html).not.toContain('file-link-chip')
  expect(html).not.toContain('node="[object Object]"')
  expect(html).toContain('desktop/')
  expect(html).toContain('src/App.tsx')
})

test('a new running turn hides previous deliveries and keeps live text free of file widgets', () => {
  const html = renderToStaticMarkup(<Conversation state={{ ...initialState, snapshot, phase: 'running', runId: 'next', stream: '正在读取 `desktop/` 中的 `src/App.tsx`。' }} openArtifact={() => {}} />)
  expect(html).not.toContain('modern-artifact-card')
  expect(html).toContain('transcript-file-link')
  expect(html).not.toContain('file-link-chip')
  expect(html).not.toContain('message-header-row')
  expect(html).not.toContain('0 项操作')
  expect(html).toContain('正在读取')
})

test('unattributed artifacts are not guessed to be newly generated', () => {
  const html = renderToStaticMarkup(<Conversation state={{ ...initialState, snapshot: { ...snapshot, runs: [] } }} openArtifact={() => {}} />)
  expect(html).not.toContain('modern-artifact-card')
  expect(html).toContain('transcript-file-link')
})
