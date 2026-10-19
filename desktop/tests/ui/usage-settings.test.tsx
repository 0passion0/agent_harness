import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { UserConfig } from '../../src/renderer/src/plugins/user_config'
import { UsageStatsContent } from '../../src/renderer/src/plugins/user_config/UsageSettings'
import type { UserSettingsPageProps } from '../../src/renderer/src/plugins/settings'
import type { UsageModel, UsageStats } from '../../src/shared/protocol'

const noop = async () => {}
const user = { userID: '42', realName: '测试用户', userName: 'test', userAvatar: '', userPhone: '', userCredit: 120, isCo: null, isBeta: null }
const props: UserSettingsPageProps = {
  initialTab: 'credits', application: {
    build_mode: 'production', projects: [], active_project_id: null, model: null,
    exa: { configured: false }, user,
    runtimes: { python: '', node: '' }, plugins: [], permission_mode: 'ask', data_dir: 'data'
  }, config: null, busy: false, apply: noop, inspect: noop, save: noop, remove: noop,
  toggleSkill: noop, testMcp: noop, importSkills: noop, testProfile: async () => '',
  readSkill: async () => null, saveSkill: noop
}

const priced: UsageModel = {
  requests: 2, reported_requests: 1, input_tokens: 10, output_tokens: 20, total_tokens: 30,
  cache_read: null, reasoning: null,
  cost_estimate: { minimum_cny: '0.012345', maximum_cny: '0.023456', minimum_credits: '1.2345', maximum_credits: '2.3456', rate_dates: ['2026-10-15'] },
  unpriced_requests: 1, jev_requests: 0, model: 'model-a', source: 'model'
}
const jev: UsageModel = { ...priced, requests: 1, reported_requests: 1, total_tokens: 7, cost_estimate: null, unpriced_requests: 0, jev_requests: 1, model: 'JEV', source: 'jev' }
const stats: UsageStats = {
  timezone: 'Asia/Shanghai', today: '2026-10-15', tracking_since: '2026-10-14', dates: ['2026-10-15'], model_names: ['model-a', 'JEV'],
  totals: { ...priced, requests: 3, reported_requests: 2, input_tokens: 10, output_tokens: 27, total_tokens: 37, jev_requests: 1 },
  models: [priced, jev], daily: [{ ...jev, date: '2026-10-15' }, { ...priced, date: '2026-10-15' }]
}

test('settings separates personal information, credits, and Token usage', () => {
  const html = renderToStaticMarkup(<UserConfig {...props}/> )
  expect(html).toContain('个人信息')
  expect(html).toContain('>研值<')
  expect(html).toContain('Token 消耗')
  expect(html).toContain('账户研值余额')
  expect(html).toContain('aria-label="统计周期"')
  expect(html).toContain('刷新')
  const profile = renderToStaticMarkup(<UserConfig {...props} initialTab="profile"/> )
  expect(profile).toContain('测试用户')
  expect(profile).toContain('用量页的研值仅为估算，不会扣减此余额')
})

test('credits view preserves decimal ranges and gives JEV zero credits in model and daily rows', () => {
  const html = renderToStaticMarkup(<UsageStatsContent mode="credits" stats={stats} user={user}/> )
  expect(html).toContain('所有非 JEV 模型使用统一费率；20 元 = 2000 研值；预计费用 = 统一费率成本 × 1.5')
  expect(html).toContain('区间反映峰谷价差和未返回的缓存明细；小数累计，不逐次取整。')
  expect(html).toContain('1.2345–2.3456 研值（¥0.012345–¥0.023456）')
  expect(html).toContain('0 研值')
  expect(html).toContain('JEV')
  expect(html).toContain('另有 1 次未计价')
})

test('Token view labels totals as reported, shows unknown fields, and explains included requests', () => {
  const html = renderToStaticMarkup(<UsageStatsContent mode="tokens" stats={stats} user={user}/> )
  expect(html).toContain('已报告 Token')
  expect(html).toContain('37')
  expect(html).toContain('未提供')
  expect(html).toContain('失败或中断且没有用量返回的请求不入账')
  expect(html).toContain('缓存读取和推理 Token 是子集')
})

test('credits view shows zero for an empty all-time selection', () => {
  const empty: UsageStats = { ...stats, tracking_since: null, dates: [], model_names: [], totals: { ...stats.totals, requests: 0, reported_requests: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, cache_read: null, reasoning: null, cost_estimate: null, unpriced_requests: 0, jev_requests: 0 }, models: [], daily: [] }
  const html = renderToStaticMarkup(<UsageStatsContent mode="credits" stats={empty} user={user}/> )
  expect(html).toContain('预计研值')
  expect(html).toContain('0 研值')
  expect(html).toContain('此筛选下暂无记录 · 预计研值 0')
})
