import { expect, test } from 'vitest'
import { initialState, reduceAgent } from './agent-state'
import type { ApplicationState } from '../../../shared/protocol'

test('application readiness opens the UI without inventing a session snapshot', () => {
  const app: ApplicationState = { build_mode: 'production', projects: [], active_project_id: null, model: null, exa: { configured: false }, user: null, runtimes: { python: '', node: '' }, plugins: [], permission_mode: 'ask', data_dir: 'private' }
  const state = reduceAgent(initialState, { event: 'app.ready', data: app })
  expect(state.phase).toBe('idle')
  expect(state.snapshot).toBeNull()
})

test('session readiness clears the previous project conversation', () => {
  const previous = { ...initialState, phase: 'question' as const, stream: 'stale', runId: 'run', question: { interaction_id: 'i', plugin_id: 'user_config', kind: 'approval', question: 'Approve?' } }
  const snapshot = { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], runs: [], totals: {}, unsettled_history: false }
  const state = reduceAgent(previous, { event: 'ready', data: snapshot })
  expect(state.snapshot).toBe(snapshot)
  expect(state.question).toBeNull()
  expect(state.stream).toBe('')
})

test('late run acknowledgement does not hide an approval request', () => {
  const question = { interaction_id: 'i', plugin_id: 'user_config', kind: 'approval', question: 'Approve?' }
  const previous = { ...initialState, phase: 'question' as const, runId: 'backend-run', question }
  expect(reduceAgent(previous, { event: 'run.accepted', run_id: 'command-id' })).toBe(previous)
})
