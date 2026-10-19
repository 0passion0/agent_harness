import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { WorkflowRuns, stepStatus, workflowGroups, workflowSteps } from '../../src/renderer/src/components/WorkflowRuns'
import { Conversation } from '../../src/renderer/src/components/Conversation'
import type { RunRecord, Snapshot } from '../../src/shared/protocol'
import type { AgentState } from '../../src/renderer/src/state/agent-state'
import { initialState, reduceAgent } from '../../src/renderer/src/state/agent-state'

function workflow(operationId: string, status: string, seq: number): RunRecord {
  return { kind: 'workflow', seq, operation_id: operationId, plugin_id: 'research', workflow_name: 'literature_search', status }
}

function node(operationId: string, rootId: string, seq: number): RunRecord {
  return { kind: 'workflow.node', seq, operation_id: operationId, parent_ids: [rootId], plugin_id: 'research', workflow_name: 'literature_search', node: 'search', status: 'success', summary: '读取引用' }
}

test('groups each workflow invocation with its descendant tools and terminal event', () => {
  const records: RunRecord[] = [
    workflow('workflow-1', 'running', 1),
    node('node-1', 'workflow-1', 2),
    { kind: 'tool_start', seq: 3, operation_id: 'tool-1', parent_ids: ['workflow-1'], call_id: 'call-1', name: 'fetch_refs', status: 'running' },
    { kind: 'tool_end', seq: 4, operation_id: 'tool-1', parent_ids: ['workflow-1'], call_id: 'call-1', name: 'fetch_refs', status: 'success', output: '找到 3 条引用' },
    workflow('workflow-1', 'completed', 5),
    workflow('workflow-2', 'running', 6),
    node('node-2', 'workflow-2', 7),
    { kind: 'tool_start', seq: 8, operation_id: 'tool-2', parent_ids: ['workflow-2'], call_id: 'call-2', name: 'verify_refs', status: 'running' },
    { kind: 'tool_end', seq: 9, operation_id: 'tool-2', parent_ids: ['workflow-2'], call_id: 'call-2', name: 'verify_refs', status: 'success', output: '核查完成' },
    workflow('workflow-2', 'failed', 10)
  ]
  const groups = workflowGroups(records)
  expect(groups.map(group => group.operationId)).toEqual(['workflow-1', 'workflow-2'])
  expect(groups[0].records.map(record => record.seq)).toEqual([1, 2, 3, 4, 5])
  expect(groups[1].records.map(record => record.seq)).toEqual([6, 7, 8, 9, 10])
  expect(workflowSteps(groups[0].records).map(record => record.seq)).toEqual([2, 4])
  expect(workflowSteps(groups[1].records).map(record => record.seq)).toEqual([7, 9])
  const markup = renderToStaticMarkup(<WorkflowRuns records={records} />)
  expect(markup.match(/class="workflow-card"/g)).toHaveLength(2)
  expect(markup).toContain('已完成')
  expect(markup).toContain('失败')

  const inProgress = renderToStaticMarkup(<WorkflowRuns records={[workflow('workflow-running', 'running', 11), node('node-done', 'workflow-running', 12)]} />)
  expect(inProgress).toContain('运行中')
})

test('places each saved run under the human message that started it', () => {
  const first = [workflow('workflow-1', 'completed', 1), node('node-1', 'workflow-1', 2)]
  const second = [workflow('workflow-2', 'completed', 3), node('node-2', 'workflow-2', 4)]
  const snapshot: Snapshot = {
    session_id: 's', thread_id: 't', thread_ids: ['t'],
    messages: [
      { type: 'human', data: { id: 'user-1', content: 'First request' } },
      { type: 'ai', data: { id: 'assistant-1', content: 'First answer' } },
      { type: 'human', data: { id: 'user-2', content: 'Second request' } },
      { type: 'ai', data: { id: 'assistant-2', content: 'Second answer' } }
    ],
    attachments: [], artifacts: [], unsettled_history: false, runs: [
      { id: 'run-1', started_at: 'now', finished_at: 'now', status: 'complete', records: first, user_message_id: 'user-1', requests: [], tools: {}, elapsed: 1 },
      { id: 'run-2', started_at: 'now', finished_at: 'now', status: 'complete', records: second, user_message_id: 'user-2', requests: [], tools: {}, elapsed: 1 }
    ], totals: {}
  }
  const state = { ...initialState, snapshot } as AgentState
  const html = renderToStaticMarkup(<Conversation state={state} openArtifact={() => {}} />)
  const firstUser = html.indexOf('First request')
  const firstCard = html.indexOf('data-operation-id="workflow-1"')
  const secondUser = html.indexOf('Second request')
  const secondCard = html.indexOf('data-operation-id="workflow-2"')
  expect(firstUser).toBeLessThan(firstCard)
  expect(firstCard).toBeLessThan(secondUser)
  expect(secondUser).toBeLessThan(secondCard)
})

test('restores an active workflow under its message while an interaction is open', () => {
  const root = workflow('workflow-active', 'running', 1)
  const runningNode = { ...node('node-active', 'workflow-active', 2), status: 'running', summary: 'Searching' }
  const activeRun = { id: 'run-active', started_at: 'now', records: [root, runningNode], user_message_id: 'user-1' }
  const snapshot: Snapshot = {
    session_id: 's', thread_id: 't', thread_ids: ['t'],
    messages: [{ type: 'human', data: { id: 'user-1', content: 'Find sources' } }],
    attachments: [], artifacts: [], unsettled_history: false, runs: [], active_run: activeRun, totals: {}
  }
  const base = { ...initialState, snapshot, runId: 'run-active' } as AgentState
  const waiting = renderToStaticMarkup(<Conversation state={{ ...base, phase: 'question' }} openArtifact={() => {}} />)
  expect(waiting.indexOf('Find sources')).toBeLessThan(waiting.indexOf('data-operation-id="workflow-active"'))
  expect(waiting).toContain('已暂停')

  const resumed = renderToStaticMarkup(<Conversation state={{ ...base, phase: 'running' }} openArtifact={() => {}} />)
  expect(resumed).toContain('运行中')
  expect(resumed).not.toContain('已暂停')
})

test('failed or cancelled workflows do not leave running descendants', () => {
  const runningTool: RunRecord = { kind: 'tool_start', operation_id: 'tool-1', status: 'running' }
  const runningNode: RunRecord = { kind: 'workflow.node', operation_id: 'node-1', status: 'running' }
  expect(stepStatus(runningTool, 'failed')).toBe('failed')
  expect(stepStatus(runningNode, 'cancelled')).toBe('cancelled')
  expect(stepStatus({ ...runningNode, status: 'success' }, 'failed')).toBe('success')
})

test('keeps the live workflow card after a long streamed model reply', () => {
  let state = reduceAgent(initialState, { event: 'run.accepted', run_id: 'r', text: 'Find sources' })
  state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: workflow('workflow-long', 'running', 1) })
  state = reduceAgent(state, { event: 'run.batch', frames: Array.from({ length: 900 }, () => ({ event: 'run.event' as const, session_id: 's', run_id: 'r', data: { kind: 'text', text: 'x' } })) })
  const html = renderToStaticMarkup(<Conversation state={state} openArtifact={() => {}} />)
  expect(html).toContain('data-operation-id="workflow-long"')
  expect(html).toContain('运行中')
})
