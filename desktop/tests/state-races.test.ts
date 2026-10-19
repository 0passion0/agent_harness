import { expect, it } from 'vitest'
import { initialState, reduceAgent } from '../src/renderer/src/state/agent-state'

it('ignores a stop acknowledgement that arrives after the run has finished', () => {
  const idle = { ...initialState, phase: 'idle' as const, lastFinishedRunId: 'run' }
  expect(reduceAgent(idle, { event: 'run.stopping' })).toBe(idle)
})

it('keeps the next question when the previous answer acknowledgement arrives late', () => {
  const question = { ...initialState, phase: 'question' as const, runId: 'run', question: { interaction_id: 'next', plugin_id: 'data_analysis', kind: 'research_question', question: '第二问' } }
  expect(reduceAgent(question, { event: 'interaction.answered', interaction_id: 'previous' })).toBe(question)
  expect(reduceAgent(question, { event: 'interaction.answered', interaction_id: 'next' })).toMatchObject({ phase: 'running', question: null })
})
