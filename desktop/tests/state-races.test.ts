import { expect, it } from 'vitest'
import { initialState, reduceAgent } from '../src/renderer/src/state/agent-state'
import type { EventFrame, Snapshot } from '../src/shared/protocol'

it('ignores background output, questions, errors and completion without stealing the active session', () => {
  const snapshot: Snapshot = { session_id: 'selected', thread_id: 'selected', thread_ids: ['selected'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false }
  const selected = reduceAgent(initialState, { event: 'ready', data: snapshot })
  const background: EventFrame[] = [
    { event: 'run.event', session_id: 'background', run_id: 'other', data: { kind: 'text', text: 'private background output' } },
    { event: 'interaction.request', session_id: 'background', run_id: 'other', data: { interaction_id: 'approval', kind: 'approval', question: 'approve?' } },
    { event: 'run.finished', session_id: 'background', run_id: 'other', data: { status: 'complete', snapshot: { ...snapshot, session_id: 'background' } } },
    { event: 'protocol.error', session_id: 'background', data: { message: 'background failure' } }
  ]
  for (const frame of background) expect(reduceAgent(selected, frame)).toBe(selected)
  expect(reduceAgent(selected, { event: 'run.accepted', session_id: 'background', run_id: 'other' })).toBe(selected)
})

it('restores a running or paused session and keeps its tool records when revisited', () => {
  const snapshot: Snapshot = { session_id: 's', thread_id: 's', thread_ids: ['s'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false,
    active_run: { id: 'run', phase: 'running', started_at: 'now', records: [{ kind: 'tool_start', call_id: 'read', status: 'running' }] } }
  const running = reduceAgent(initialState, { event: 'snapshot.loaded', data: snapshot })
  expect(running).toMatchObject({ phase: 'running', runId: 'run', tools: { read: { status: 'running' } } })
  const question = { interaction_id: 'confirm', kind: 'approval', question: '允许？' }
  const paused = reduceAgent(running, { event: 'snapshot.loaded', data: { ...snapshot, active_run: { ...snapshot.active_run!, phase: 'question', question } } })
  expect(paused).toMatchObject({ phase: 'question', runId: 'run', question })
})

it('ignores a stop acknowledgement that arrives after the run has finished', () => {
  const idle = { ...initialState, phase: 'idle' as const, lastFinishedRunId: 'run' }
  expect(reduceAgent(idle, { event: 'run.stopping' })).toBe(idle)
})

it('keeps the next question when the previous answer acknowledgement arrives late', () => {
  const question = { ...initialState, phase: 'question' as const, runId: 'run', question: { interaction_id: 'next', plugin_id: 'data_analysis', kind: 'research_question', question: '第二问' } }
  expect(reduceAgent(question, { event: 'interaction.answered', interaction_id: 'previous' })).toBe(question)
  expect(reduceAgent(question, { event: 'interaction.answered', interaction_id: 'next' })).toMatchObject({ phase: 'running', question: null })
})
