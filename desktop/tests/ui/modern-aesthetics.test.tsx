import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { FileLink, isFilePath, cleanPath } from '../../src/renderer/src/components/FileLink'
import { InlineActivity } from '../../src/renderer/src/components/InlineActivity'
import { Conversation } from '../../src/renderer/src/components/Conversation'
import { initialState, reduceAgent } from '../../src/renderer/src/state/agent-state'
import type { RunRecord, Snapshot } from '../../src/shared/protocol'

test('isFilePath detects valid workspace and system file paths', () => {
  expect(isFilePath('src/renderer/src/App.tsx')).toBe(true)
  expect(isFilePath('package.json')).toBe(true)
  expect(isFilePath('./README.md')).toBe(true)
  expect(isFilePath('C:/workspace/project/main.py')).toBe(true)
  expect(isFilePath('file:///k:/agent_harness/test.txt')).toBe(true)
  expect(isFilePath('packages/')).toBe(true)

  // Should reject non-paths
  expect(isFilePath('hello world this is a normal sentence')).toBe(false)
  expect(isFilePath('')).toBe(false)
  expect(isFilePath('a')).toBe(false)
})

test('cleanPath removes file protocol and line anchors', () => {
  expect(cleanPath('file:///k:/agent/test.ts#L10-L20')).toBe('k:/agent/test.ts')
  expect(cleanPath('src/App.tsx:25')).toBe('src/App.tsx')
})

test('FileLink renders interactive file chip with file icon and actions', () => {
  const html = renderToStaticMarkup(<FileLink path="src/renderer/src/App.tsx" />)
  expect(html).toContain('file-link-chip')
  expect(html).toContain('App.tsx')
  expect(html).toContain('在资源管理器中定位')
  expect(html).toContain('复制路径')
  expect(html).toContain('<button type="button" class="file-chip-main"')
  expect(html).not.toContain('role="button"')
})

test('long report tables scroll independently and saved messages have no empty header row', () => {
  const html = renderToStaticMarkup(<Conversation openArtifact={() => {}} state={{
    ...initialState,
    snapshot: {
      session_id: 's', thread_id: 't', thread_ids: ['t'], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false,
      messages: [{ type: 'ai', data: { content: '# 测试结果\n\n| 输入 | 结果 |\n| --- | --- |\n| [商品表](data/products.csv) | `artifact SHA256 does not match execution receipt` |' } }]
    }
  }} />)
  expect(html).toContain('class="message-table-scroll" role="region"')
  expect(html).toContain('tabindex="0"><table>')
  expect(html).not.toContain('message-header-row')
  expect(html).toContain('aria-label="打开 商品表"')
  expect(html).not.toContain('file-chip-sub')
})

test('InlineActivity renders active tools and completed executions beautifully', () => {
  const html = renderToStaticMarkup(<InlineActivity
    running={true}
    records={[
      { kind: 'tool_start', seq: 1, call_id: 'call-1', name: 'read_file', status: 'running', arguments: { path: 'src/App.tsx' } },
      { kind: 'tool_end', seq: 2, call_id: 'call-2', name: 'search_files', status: 'success', output: { results: ['src/App.tsx'] }, elapsed: 0.15 }
    ]}
  />)
  expect(html).toContain('inline-activity-card')
  expect(html).toContain('read_file')
  expect(html).toContain('search_files')
  expect(html).toContain('0.15s')
  expect(html).toContain('is-running')
})

test('merges tool starts, ends and the tool dictionary without merging separate calls', () => {
  const records: RunRecord[] = [
    { kind: 'tool_start', call_id: 'one', name: 'read_file', status: 'running', arguments: { path: 'one.csv' } },
    { kind: 'tool_end', call_id: 'one', name: 'read_file', status: 'success', output: 'ok' },
    { kind: 'tool_start', call_id: 'two', name: 'read_file', status: 'running' }
  ]
  const html = renderToStaticMarkup(<InlineActivity records={records} tools={{one: records[1], two: records[2]}} running />)
  expect(html).toContain('2 项操作')
  expect(html.match(/class="act-item /g)).toHaveLength(2)
  expect(html.match(/class="act-item is-running"/g)).toHaveLength(1)
  expect(html).toContain('<details class="inline-activity-card is-active"><summary')
  const paused = renderToStaticMarkup(<InlineActivity records={[records[2]]} status="paused" />)
  expect(paused).toContain('等待你的答复')
  expect(paused).not.toContain('is-running')
  expect(paused).not.toContain('is-complete')
  const cancelled = renderToStaticMarkup(<InlineActivity records={[records[2]]} status="cancelled" />)
  expect(cancelled).toContain('已取消')
  expect(cancelled).not.toContain('is-running')
  const failed = renderToStaticMarkup(<InlineActivity records={[{...records[2], kind: 'tool_error', status: 'failed'}]} status="failed" />)
  expect(failed).toContain('1 失败')
  expect(failed).not.toContain('is-complete')
})

test('keeps ordinary tool history after the final answer, after reload and in the correct turn', () => {
  const records: RunRecord[] = [
    { kind: 'tool_start', call_id: 'one', name: 'read_file', status: 'running', arguments: {path: 'data.csv'} },
    { kind: 'tool_end', call_id: 'one', name: 'read_file', status: 'success', output: 'ok' }
  ]
  const snapshot: Snapshot = {
    session_id: 's', thread_id: 't', thread_ids: ['t'], attachments: [], artifacts: [], totals: {}, unsettled_history: false,
    messages: [{type:'human', data:{id:'u1',content:'第一轮问题'}},{type:'ai',data:{content:'第一轮答案'}},{type:'human',data:{id:'u2',content:'第二轮问题'}}],
    runs: [{id:'r',user_message_id:'u1',status:'complete',records,tools:{},requests:[],elapsed:1}]
  }
  let state = reduceAgent(initialState, {event:'run.accepted',run_id:'r'})
  for (const data of records) state = reduceAgent(state, {event:'run.event',session_id:'s',run_id:'r',data})
  expect(state.tools.one.arguments).toEqual({path:'data.csv'})
  state = reduceAgent(state, {event:'run.finished',session_id:'s',run_id:'r',data:{status:'complete',snapshot}})
  for (const current of [state, reduceAgent(state, {event:'snapshot.loaded',data:snapshot})]) {
    const html = renderToStaticMarkup(<Conversation state={current} openArtifact={() => {}} />)
    expect(html.indexOf('第一轮答案')).toBeLessThan(html.indexOf('saved-run-activity'))
    expect(html.indexOf('saved-run-activity')).toBeLessThan(html.indexOf('第二轮问题'))
    expect(html).toContain('read_file')
    expect(html).toContain('1 项操作')
    expect(html.match(/class="act-item /g)).toHaveLength(1)
    expect(html).not.toContain('is-active-response')
  }
})

test('Conversation renders copy buttons, file chips and code block headers in markdown', () => {
  const html = renderToStaticMarkup(<Conversation
    openArtifact={() => {}}
    state={{
      ...initialState,
      phase: 'running',
      stream: '正在流式响应 `src/App.tsx` 中的代码：\n```typescript\nconst x = 1\n```',
      snapshot: {
        session_id: 's', thread_id: 't', thread_ids: ['t'], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false,
        messages: [
          { type: 'human', data: { content: '请检查 `package.json`' } },
          { type: 'ai', data: { content: '已完成，请查看 `docs/README.md`' } }
        ]
      }
    }}
  />)

  // Message copy buttons
  expect(html).toContain('msg-copy-btn')
  expect(html).toContain('复制全部记录')

  // Code block header with copy button and language tag
  expect(html).toContain('modern-code-block')
  expect(html).toContain('modern-code-lang')
  expect(html).toContain('TYPESCRIPT')
  expect(html).toContain('modern-code-copy')

  // File link chips rendered inside message
  expect(html).toContain('file-link-chip')
  expect(html).toContain('package.json')
  expect(html).toContain('README.md')
  expect(html).toContain('App.tsx')

  // Streaming cursor
  expect(html).toContain('streaming-cursor')
})
