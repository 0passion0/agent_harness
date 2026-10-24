import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { RunRecord, Snapshot } from '../../src/shared/protocol'
import { initialState, reduceAgent, type AgentState } from '../../src/renderer/src/state/agent-state'
import { runTimeline, savedConversation } from '../../src/renderer/src/components/run-timeline'
import { Conversation } from '../../src/renderer/src/components/Conversation'

const records: RunRecord[] = [
  { kind: 'model_start', operation_id: 'plan' },
  { kind: 'text', operation_id: 'plan', message_id: 'a1', text: '先检查' },
  { kind: 'text', operation_id: 'plan', message_id: 'a1', text: '当前实现。' },
  { kind: 'model_end', operation_id: 'plan', message_id: 'a1', text: '先检查当前实现。' },
  { kind: 'tool_start', call_id: 'read', name: 'read_file', arguments: { path: 'src/example.ts' }, status: 'running' },
  { kind: 'model_start', operation_id: 'progress' },
  { kind: 'text', operation_id: 'progress', message_id: 'a2', text: '已找到分组问题，开始修复。' },
  { kind: 'model_end', operation_id: 'progress', message_id: 'a2', text: '已找到分组问题，开始修复。' },
  { kind: 'tool_start', call_id: 'verify', name: 'shell_exec', arguments: { command: 'npm run typecheck' }, status: 'running' },
  { kind: 'tool_end', call_id: 'read', name: 'read_file', output: 'source code', status: 'success' },
  { kind: 'tool_error', call_id: 'verify', name: 'shell_exec', output: 'Type error', status: 'failed' },
  { kind: 'model_end', operation_id: 'answer', message_id: 'a3', text: '检查结束，有一项错误需要修复。' }
]

const snapshot: Snapshot = {
  session_id: 's', thread_id: 't', thread_ids: ['t'], attachments: [], artifacts: [], totals: {}, unsettled_history: false,
  messages: [
    { type: 'human', data: { id: 'u1', content: '检查流式对话' } },
    { type: 'ai', data: { id: 'a1', content: [{ type: 'text', text: '先检查当前实现。' }] } },
    { type: 'ai', data: { content: '' } },
    { type: 'tool', data: { content: 'source code' } },
    { type: 'ai', data: { id: 'a3', content: '检查结束，有一项错误需要修复。' } },
    { type: 'human', data: { id: 'u2', content: '下一轮问题' } },
    { type: 'ai', data: { content: '先检查当前实现。' } }
  ],
  runs: [{ id: 'r', user_message_id: 'u1', status: 'failed', records, tools: {}, requests: [], elapsed: 83 }]
}

function render(state: AgentState) {
  return renderToStaticMarkup(<Conversation state={state} openArtifact={() => {}} />)
}

function expectOrder(html: string) {
  const markers = ['先检查当前实现。', '已读取', '已找到分组问题，开始修复。', '未能运行', '检查结束，有一项错误需要修复。']
  markers.slice(1).forEach((marker, index) => expect(html.indexOf(markers[index])).toBeLessThan(html.indexOf(marker)))
  expect(html.match(/class="run-tool-group|class="run-segment run-tool-group/g)).toHaveLength(2)
  expect(html.match(/class="act-item /g)).toHaveLength(2)
  expect(html).toContain('is-error')
}

test('interleaves narration and tools, keeping delayed results in their original group', () => {
  const blocks = runTimeline(records)
  expect(blocks.map(block => block.kind)).toEqual(['text', 'tools', 'text', 'tools', 'text'])
  expect(blocks[1]).toMatchObject({ records: [{ call_id: 'read', status: 'success', arguments: { path: 'src/example.ts' }, output: 'source code' }] })
  expect(blocks[3]).toMatchObject({ records: [{ call_id: 'verify', status: 'failed', arguments: { command: 'npm run typecheck' } }] })
  expect(blocks[0]).toMatchObject({ text: '先检查当前实现。', complete: true, messageId: 'a1' })
})

test('batched streaming keeps model boundaries and more than 800 deltas without a card per token', () => {
  let state = reduceAgent(initialState, { event: 'run.accepted', session_id: 's', run_id: 'r' })
  state = reduceAgent(state, { event: 'run.batch', frames: records.map(data => ({ event: 'run.event', session_id: 's', run_id: 'r', data })) })
  expectOrder(render(state))
  state = reduceAgent(state, { event: 'run.batch', frames: Array.from({ length: 900 }, () => ({ event: 'run.event' as const, session_id: 's', run_id: 'r', data: { kind: 'text', operation_id: 'next', text: 'x' } })) })
  expect(state.events).toHaveLength(800)
  expect(state.runLog.at(-1)).toMatchObject({ text: 'x'.repeat(900) })
  const blocks = runTimeline(state.runLog, state.tools)
  expect(blocks.filter(block => block.kind === 'text')).toHaveLength(4)
  expect(blocks.at(-1)).toMatchObject({ text: 'x'.repeat(900), complete: false })
  expectOrder(render(state))
  expect(render(state).match(/message-markdown-wrap is-streaming/g)).toHaveLength(1)
})

test('saved and reloaded runs show narration once, preserving later turns and record-only progress', () => {
  const entries = savedConversation(snapshot)
  expect(entries.map(entry => entry.kind)).toEqual(['message', 'run', 'message', 'message'])
  const finished = reduceAgent(initialState, { event: 'run.finished', session_id: 's', run_id: 'r', data: { status: 'failed', snapshot } })
  for (const state of [finished, reduceAgent(initialState, { event: 'snapshot.loaded', data: snapshot })]) {
    const html = render(state)
    expectOrder(html)
    expect(html.match(/先检查当前实现。/g)).toHaveLength(2)
    expect(html.match(/检查结束，有一项错误需要修复。/g)).toHaveLength(1)
    expect(html.indexOf('检查结束，有一项错误需要修复。')).toBeLessThan(html.indexOf('下一轮问题'))
    expect(html).toContain('用时 1分23秒')
    expect(html).not.toContain('is-active-response')
  }
})

test('paused snapshot resumes in place without duplicating checkpoint narration', () => {
  const paused: Snapshot = { ...snapshot, messages: snapshot.messages.slice(0, 3), runs: [], active_run: { id: 'r', user_message_id: 'u1', started_at: 'now', phase: 'question' as const, records: records.slice(0, 5) } }
  let state = reduceAgent(initialState, { event: 'snapshot.loaded', data: paused })
  expect(render(state).match(/先检查当前实现。/g)).toHaveLength(1)
  expect(render(state)).toContain('等待你的答复')
  state = reduceAgent(state, { event: 'run.batch', frames: records.slice(5).map(data => ({ event: 'run.event', session_id: 's', run_id: 'r', data })) })
  expectOrder(render(state))
  expect(render(state).match(/先检查当前实现。/g)).toHaveLength(1)
})

test('old text events without IDs still split at tools and model ends complete their own text', () => {
  const blocks = runTimeline([
    { kind: 'text', text: ['先', { text: '检查' }] },
    { kind: 'tool_end', call_id: 'one', name: 'read_file', status: 'success' },
    { kind: 'text', text: '然后修改' },
    { kind: 'model_end', message_id: 'last', text: '然后修改并验证' }
  ])
  expect(blocks.map(block => block.kind)).toEqual(['text', 'tools', 'text'])
  expect(blocks[0]).toMatchObject({ text: '先检查' })
  expect(blocks[2]).toMatchObject({ text: '然后修改并验证', complete: true, messageId: 'last' })
})

test('subagent output stays out of assistant narration while its tools remain inspectable', () => {
  const blocks = runTimeline([
    { kind: 'text', operation_id: 'main', text: '主流程说明' },
    { kind: 'model_delta', operation_id: 'child', agent_name: 'research', text: '内部过程' },
    { kind: 'model_end', operation_id: 'child', agent_name: 'research', text: '内部总结' },
    { kind: 'tool_start', call_id: 'child-tool', agent_name: 'research', name: 'search', status: 'running' }
  ], { 'child-tool': { kind: 'tool_end', status: 'success', output: 'found' } })
  expect(blocks.map(block => block.kind)).toEqual(['text', 'tools'])
  expect(blocks[1]).toMatchObject({ records: [{ call_id: 'child-tool', status: 'success', output: 'found' }] })
})

test('completed responses without IDs do not swallow the next response or a final-only answer', () => {
  const blocks = runTimeline([
    { kind: 'text', text: '初步说明' },
    { kind: 'model_end', text: '初步说明' },
    { kind: 'text', text: '下一阶段' },
    { kind: 'model_end', text: '下一阶段' },
    { kind: 'tool_end', call_id: 'old', name: 'read_file', status: 'success' },
    { kind: 'model_end', text: '最终结论' }
  ])
  expect(blocks.filter(block => block.kind === 'text').map(block => block.text)).toEqual(['初步说明', '下一阶段', '最终结论'])
})
