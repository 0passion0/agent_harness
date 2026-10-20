import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RunDetails } from '../../src/renderer/src/components/RunDetails'
import { initialState, reduceAgent } from '../../src/renderer/src/state/agent-state'
import type { Snapshot } from '../../src/shared/protocol'

test('updates tokens and credits during a run without changing or double counting the saved snapshot', () => {
  const snapshot: Snapshot = { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], unsettled_history: false, runs: [],
    totals: { complete: true, total_tokens: 100 } }
  const totals = { complete: false, total_tokens: 150, cost_estimate: {
    minimum_cny: '0.01524', maximum_cny: '0.03048', minimum_credits: '1.524', maximum_credits: '3.048', rate_dates: ['2026-10-15'] } }
  let state = reduceAgent(initialState, { event: 'ready', data: snapshot })
  state = reduceAgent(state, { event: 'run.accepted', run_id: 'r' })
  state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: { kind: 'model_end', totals } })
  state = reduceAgent(state, { event: 'run.event', session_id: 's', run_id: 'r', data: { kind: 'text', text: 'answer' } })
  state = reduceAgent(state, { event: 'run.accepted', run_id: 'r' })
  const html = renderToStaticMarkup(<RunDetails state={state} />)
  expect(html).toContain('会话累计 Token：150（用量未完整返回）')
  expect(html).toContain('1.524–3.048 研值')
  expect(state.snapshot).toBe(snapshot)
  expect(state.snapshot?.totals.total_tokens).toBe(100)

  const saved = { ...snapshot, totals: { ...totals, complete: true } }
  state = reduceAgent(state, { event: 'run.finished', session_id: 's', run_id: 'r', data: { status: 'complete', snapshot: saved } })
  expect(state.liveTotals).toBeNull()
  expect(renderToStaticMarkup(<RunDetails state={state} />)).toContain('会话累计 Token：150</p>')
  expect(reduceAgent({ ...state, liveTotals: totals }, { event: 'run.accepted', run_id: 'next' }).liveTotals).toBeNull()
  expect(reduceAgent({ ...state, liveTotals: totals }, { event: 'ready', data: snapshot }).liveTotals).toBeNull()
})

test('shows fractional credit estimate and explicitly excludes unpriced requests', () => {
  const html = renderToStaticMarkup(<RunDetails state={{ ...initialState,
    snapshot: { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], unsettled_history: false, runs: [],
      totals: { complete: false, total_tokens: 12000, unpriced_requests: 2,
        cost_estimate: { minimum_cny: '0.01524', maximum_cny: '0.03048', minimum_credits: '1.524', maximum_credits: '3.048', rate_dates: ['2026-10-15'] } }
    }
  }} />)
  for (const text of ['1.524', '3.048', '0.03048', '加价 50%', '小数累计', '未实际扣费', '2', '次请求未计价', '2026-10-15']) expect(html).toContain(text)
  expect(html).not.toContain('暂无可计价用量')
})

test('does not show a zero bill for absent pricing', () => {
  expect(renderToStaticMarkup(<RunDetails state={initialState} />)).toContain('暂无可计价用量')
})

test('shows peak estimate in summary and per-model usage inside expandable details', () => {
  const estimate = (min: string, max: string) => ({ minimum_cny: min, maximum_cny: max, minimum_credits: `c${min}`, maximum_credits: max, rate_dates: ['2026-10-15'] })
  const html = renderToStaticMarkup(<RunDetails state={{ ...initialState,
    snapshot: { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], unsettled_history: false, runs: [],
      totals: { complete: true, total_tokens: 3000, unpriced_requests: 0, cost_estimate: estimate('1', '6.5'), models: [
        { model: 'model-a', requests: 2, input_tokens: 1000, output_tokens: 500, total_tokens: 1500, cache_hit_tokens: 400, cost_estimate: estimate('1', '3'), unpriced_requests: 0, jev_requests: 0 },
        { model: 'model-b', requests: 1, input_tokens: 1000, output_tokens: 500, total_tokens: 1500, cache_hit_tokens: null, cost_estimate: null, unpriced_requests: 1, jev_requests: 0 }] } }
  }} />)
  expect(html).toContain('最多约 6.50 研值')
  const details = html.slice(html.indexOf('<details class="usage-detail">'))
  for (const text of ['查看用量与费用明细', 'model-a', 'model-b', '缓存命中 400', '缓存命中 未返回', '未计价', '下限按输入全部命中缓存']) expect(details).toContain(text)
})

test('JEV-only usage is explicitly zero credits while retaining tokens', () => {
  const html = renderToStaticMarkup(<RunDetails state={{ ...initialState,
    snapshot: { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], unsettled_history: false, runs: [],
      totals: { complete: true, total_tokens: 13, requests: 1, jev_requests: 1, unpriced_requests: 0, cost_estimate: null } }
  }} />)
  expect(html).toContain('13')
  expect(html).toContain('0 研值')
  expect(html).toContain('JEV 请求仅统计 Token，不计研值')
  expect(html).not.toContain('次请求未计价')
})

test('shows ordered live events and saved model and tool details', () => {
  const html = renderToStaticMarkup(<RunDetails state={{ ...initialState, phase: 'question',
    question: { interaction_id: 'i', kind: 'question', question: '是否继续？' },
    runLog: [
      { kind: 'model_start', model: 'test-model', status: 'running' },
      { kind: 'model_error', model: 'test-model', status: 'failed', elapsed: 1.25, error: 'ConnectionError' }
    ],
    snapshot: { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], unsettled_history: false,
      totals: { complete: true, total_tokens: 42 },
      runs: [{ elapsed: 2.5, requests: [{ index: 1, model: 'saved-model', status: 'complete', elapsed: 1, usage: { total_tokens: 42 } }],
        tools: { call1: { name: 'run_python', status: 'success', elapsed: 0.5, arguments: { code: 'print(123)' }, output: 'saved-output' } } }]
    }
  }} />)
  expect(html.indexOf('模型请求开始')).toBeLessThan(html.indexOf('模型请求失败'))
  for (const value of ['是否继续？', 'ConnectionError', '1.25', 'saved-model', 'total_tokens', 'run_python', 'call1', 'print(123)', 'saved-output', '0.50', '2.50']) expect(html).toContain(value)
})

test('shows full model input and output in live events and saved requests', () => {
  const input = { messages: [[{ type: 'system', content: 'system-prompt' }, { type: 'human', content: 'user-question' }]], parameters: { model: 'test-model' } }
  const output = { generations: [[{ message: { type: 'ai', content: 'model-answer', tool_calls: [{ name: 'run_python', args: { code: 'print(456)' } }] } }]], llm_output: { provider: 'test-provider' } }
  const html = renderToStaticMarkup(<RunDetails state={{ ...initialState,
    runLog: [{ kind: 'model_start', input, output: null }, { kind: 'model_end', input, output }],
    snapshot: { session_id: 's', thread_id: 't', thread_ids: ['t'], messages: [], attachments: [], artifacts: [], unsettled_history: false,
      totals: { complete: true, total_tokens: 42 },
      runs: [{ elapsed: 1, requests: [{ index: 1, model: 'saved-model', status: 'complete', input, output }], tools: {} }]
    }
  }} />)
  expect(html.match(/<h4>模型输入<\/h4>/g)).toHaveLength(3)
  expect(html.match(/<h4>模型输出<\/h4>/g)).toHaveLength(3)
  for (const value of ['system-prompt', 'user-question', 'model-answer', 'tool_calls', 'print(456)', 'test-provider', '未返回完整模型输出。']) expect(html).toContain(value)
})
