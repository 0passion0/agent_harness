import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { UserConfig, type UserSettingsPageProps } from '../../src/renderer/src/plugins/user_config'
import { UsageStatsContent } from '../../src/renderer/src/plugins/user_config/UsageSettings'
import type { UsageModel, UsageStats } from '../../src/shared/protocol'

const noop = async () => {}
const props: UserSettingsPageProps = {
  initialTab: 'usage', application: {
    build_mode: 'production', projects: [], active_project_id: null, model: null,
    skills: [],
    runtimes: { python: '', node: '' }, plugins: [], permission_mode: 'ask', data_dir: 'data'
  }, config: null, busy: false, apply: noop, inspect: noop, save: noop, remove: noop,
  toggleSkill: noop, testMcp: noop, importSkills: noop, testProfile: async () => '',
  readSkill: async () => null, saveSkill: noop
}

const priced: UsageModel = {
  requests: 2, reported_requests: 1, input_tokens: 10, output_tokens: 20, total_tokens: 30,
  cache_read: null, reasoning: null,
  cost_estimate: null,
  unpriced_requests: 0, jev_requests: 0, model: 'model-a', source: 'model'
}
const jev: UsageModel = { ...priced, requests: 1, reported_requests: 1, total_tokens: 7, cost_estimate: null, unpriced_requests: 0, jev_requests: 1, model: 'JEV', source: 'jev' }
const stats: UsageStats = {
  timezone: 'Asia/Shanghai', today: '2026-10-15', tracking_since: '2026-10-14', dates: ['2026-10-15'], model_names: ['model-a', 'JEV'],
  totals: { ...priced, requests: 3, reported_requests: 2, input_tokens: 10, output_tokens: 27, total_tokens: 37, jev_requests: 1 },
  models: [priced, jev], daily: [{ ...jev, date: '2026-10-15' }, { ...priced, date: '2026-10-15' }]
}

test('settings shows Token usage without personal information or credits', () => {
  const html = renderToStaticMarkup(<UserConfig {...props}/>)
  expect(html).not.toContain('个人信息')
  expect(html).not.toContain('>研值<')
  expect(html).toContain('Token 消耗')
  expect(html).toContain('aria-label="统计周期"')
})

test('Token view labels totals as reported, shows unknown fields, and explains included requests', () => {
  const html = renderToStaticMarkup(<UsageStatsContent stats={stats}/>)
  expect(html).toContain('已报告 Token')
  expect(html).toContain('37')
  expect(html).toContain('未提供')
  expect(html).toContain('失败或中断且没有用量返回的请求不入账')
  expect(html).toContain('缓存读取和推理 Token 是子集')
  expect(html).toContain('model-a')
  expect(html).toContain('JEV')
})

test('Token view shows zero for an empty selection', () => {
  const empty: UsageStats = { ...stats, tracking_since: null, dates: [], model_names: [], totals: { ...stats.totals, requests: 0, reported_requests: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, cache_read: null, reasoning: null, cost_estimate: null, unpriced_requests: 0, jev_requests: 0 }, models: [], daily: [] }
  const html = renderToStaticMarkup(<UsageStatsContent stats={empty}/>)
  expect(html).toContain('Token 用量')
})
