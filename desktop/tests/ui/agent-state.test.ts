import { describe, expect, it } from 'vitest'
import { initialState, reduceAgent } from '../../src/renderer/src/state/agent-state'
import type { SessionTokenReport } from '../../src/shared/protocol'

describe('agent state', () => {
  it('updates the context popup and totals when a model request completes', () => {
    const token_usage = { used_tokens: 820 } as SessionTokenReport
    let state = reduceAgent(initialState, { event: 'ready', data: { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false } })
    state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: { kind: 'model_end', totals: { total_tokens: 820 }, token_usage } })
    expect(state.snapshot?.token_usage).toBe(token_usage)
    expect(state.liveTotals).toEqual({ total_tokens: 820 })
  })
  it('preserves the current conversation and running state during app refresh', () => {
    const snapshot = { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false }
    let state = reduceAgent(initialState, { event: 'ready', data: snapshot })
    state = reduceAgent(state, { event: 'run.accepted', session_id: 's', run_id: 'r' })
    const refreshed = reduceAgent(state, { event: 'app.ready', data: {} as never })
    expect(refreshed.snapshot).toBe(snapshot)
    expect(refreshed.runId).toBe('r')
    expect(refreshed.phase).toBe('running')
  })
  it('keeps same-name tools distinct by call ID and waits for saved finish', () => {
    let state = reduceAgent(initialState, { event: 'ready', data: { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false } })
    state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: { kind: 'tool_start', call_id: 'one', name: 'run_python', status: 'running' } })
    state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: { kind: 'tool_start', call_id: 'two', name: 'run_python', status: 'running' } })
    state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: { kind: 'tool_end', call_id: 'one', name: 'run_python', status: 'success', output: 'ok' } })
    expect(state.tools.one.status).toBe('success')
    expect(state.tools.two.status).toBe('running')
    state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: { kind: 'task_end', complete: false } })
    expect(state.phase).toBe('running')
  })

  it('discards a question after cancellation', () => {
    let state = reduceAgent(initialState, { event: 'interaction.request', session_id: 's', run_id: 'r', data: { interaction_id: 'i', kind: 'question', question: '为何？' } })
    state = reduceAgent(state, { event: 'run.finished', session_id: 's', run_id: 'r', data: { status: 'cancelled', snapshot: { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false } } })
    expect(state.question).toBeNull()
    expect(state.phase).toBe('idle')
  })

  it('keeps a saved finish authoritative when the start reply arrives late', () => {
    const snapshot = { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false }
    let state = reduceAgent(initialState, { event: 'ready', data: snapshot })
    state = reduceAgent(state, { event: 'run.finished', session_id: 's', run_id: 'r', data: { status: 'complete', snapshot } })
    state = reduceAgent(state, { event: 'run.accepted', session_id: 's', run_id: 'r' })
    expect(state.phase).toBe('idle')
    expect(state.lastRunSaved).toBe(true)
  })
  it('restores active workflow records from a reloaded snapshot', () => {
    const record = { kind: 'workflow.node', seq: 2, operation_id: 'op', node: 'search', status: 'running' }
    const snapshot = { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], active_run: { id: 'r', started_at: 'now', phase: 'question' as const, records: [record] }, totals: {}, unsettled_history: false }
    expect(reduceAgent(initialState, { event: 'ready', data: snapshot }).events).toEqual([record])
  })
  it('allows dismissing protocol errors without losing the conversation', () => {
    const snapshot = { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false }
    const ready = reduceAgent(initialState, { event: 'ready', data: snapshot })
    const failed = reduceAgent(ready, { event: 'protocol.error', data: { message: '网络异常' } })
    const cleared = reduceAgent(failed, { event: 'error.clear' })
    expect(cleared.error).toBeNull()
    expect(cleared.snapshot).toBe(snapshot)
    expect(cleared.phase).toBe('idle')
  })
})


describe('stream event aggregation', () => {
  it('retains lifecycle logs through long replies and saved finish, then resets for a new run or session', () => {
    const snapshot = { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false }
    const start = { kind: 'model_start', model: 'test-model', index: 1 }
    let state = reduceAgent(initialState, { event: 'run.accepted', session_id: 's', run_id: 'r' })
    state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: start })
    state = reduceAgent(state, { event: 'run.batch', frames: Array.from({ length: 900 }, () => ({ event: 'run.event' as const, session_id: 's', run_id: 'r', data: { kind: 'text', text: 'x' } })) })
    expect(state.runLog).toEqual([start, { kind: 'text', text: 'x'.repeat(900) }])
    expect(state.events).toHaveLength(800)
    state = reduceAgent(state, { event: 'run.finished', session_id: 's', run_id: 'r', data: { status: 'complete', snapshot } })
    expect(state.runLog).toEqual([start, { kind: 'text', text: 'x'.repeat(900) }, { kind: 'run_finished', status: 'complete' }])
    expect(reduceAgent(state, { event: 'run.accepted', session_id: 's', run_id: 'next' }).runLog).toEqual([])
    expect(reduceAgent(state, { event: 'snapshot.loaded', data: snapshot }).runLog).toEqual([])
  })
  it('preserves streamed text order when batched and bounds diagnostic records', () => {
    const frames = Array.from({ length: 900 }, (_, i) => ({ event: 'run.event' as const, session_id: 's', run_id: 'r', data: { kind: 'text', text: String(i % 10) } }))
    const result = reduceAgent(initialState, { event: 'run.batch', frames })
    expect(result.stream).toHaveLength(900)
    expect(result.stream.startsWith('0123456789')).toBe(true)
    expect(result.events.length).toBe(800)
    expect(result.runLog).toHaveLength(1)
  })

  it('retains workflow event identity and hierarchy for expandable run cards', () => {
    const record = { kind: 'workflow.node', seq: 4, operation_id: 'op-1', parent_ids: ['parent'], node: 'search', agent_name: 'Research', status: 'running', summary: 'Searching' }
    const state = reduceAgent(initialState, { event: 'run.event', session_id: 's', run_id: 'r', data: record })
    expect(state.events).toContainEqual(record)
  })
})
