/**
 * @file UsageSettings.tsx
 * @description 本机用量报表与研值估算明细组件。
 * 负责提供多维度用量筛选（按年/月/日/全部时间、按模型筛选），分别呈现：
 * 1. 研值模式 (credits)：账户研值余额、统一费率估算、按模型及每日研值估算表；
 * 2. Token 模式 (tokens)：输入/输出/总计已报告 Token、缓存读取 Token、推理 Token、按模型及每日详细用量表。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useRef, useState } from 'react'
import type { UsageModel, UsageStats, UserProfile } from '../../../../shared/protocol'

/** 统计呈现模式：研值估算 ('credits') 或 Token 消耗 ('tokens') */
type UsageMode = 'credits' | 'tokens'

/**
 * 获取当前北京时间（Asia/Shanghai）的年月字符串（YYYY-MM 格式）。
 */
function currentMonth() {
  const parts = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit' }).formatToParts(new Date())
  return `${parts.find(part => part.type === 'year')!.value}-${parts.find(part => part.type === 'month')!.value}`
}

/**
 * 格式化数值展示，空值时回退至“未提供”。
 */
function value(val: number | null) {
  return val === null ? '未提供' : val.toLocaleString()
}

/**
 * 格式化输出研值区间及未计价请求说明。
 */
function estimate(item: UsageModel) {
  if (item.requests === 0 || item.requests === item.jev_requests) return '0 研值'
  if (!item.cost_estimate) return item.unpriced_requests ? `未计价 ${item.unpriced_requests} 次` : '未提供'
  const cost = item.cost_estimate
  return <>{cost.minimum_credits}–{cost.maximum_credits} 研值（¥{cost.minimum_cny}–¥{cost.maximum_cny}）{item.unpriced_requests > 0 && ` · 另有 ${item.unpriced_requests} 次未计价`}</>
}

/**
 * 用量数据展示主体组件。
 *
 * 第一步：根据 mode 渲染总览统计卡片与费率计价说明。
 * 第二步：按模型维度渲染聚合统计表格。
 * 第三步：按天与模型双维度渲染每日时序数据明细表格。
 *
 * @param props - 模式、统计数据与用户信息
 */
export function UsageStatsContent({ mode, stats, user }: { mode: UsageMode; stats: UsageStats; user: UserProfile | null }) {
  const totals = stats.totals
  const summary: UsageModel = { ...totals, model: '', source: 'model' }

  return <>
    {/* 第一步：总览与说明卡片 */}
    <section className="pref-section usage-overview">
      <div className="pref-section-heading">
        <div>
          <h2>{mode === 'credits' ? '研值估算' : 'Token 用量'}</h2>
          <p>当前账户在本机桌面端所有项目的汇总，按北京时间统计。</p>
        </div>
        <span className="pref-state">仅本机</span>
      </div>

      {mode === 'credits' ? <>
        <div className="usage-cards">
          <div>
            <small>账户研值余额</small>
            <strong>{user ? value(user.userCredit) : '连接中…'}</strong>
            <span>由 MedPeer 账户提供</span>
          </div>
          <div>
            <small>预计研值</small>
            <strong>{estimate(summary)}</strong>
            <span>JEV 研值为 0</span>
          </div>
        </div>
        <div className="usage-estimate">
          <strong>计价说明</strong>
          <span>所有非 JEV 模型使用统一费率；20 元 = 2000 研值；预计费用 = 统一费率成本 × 1.5</span>
          <small>区间反映峰谷价差和未返回的缓存明细；小数累计，不逐次取整。{totals.cost_estimate?.rate_dates.length ? ` 价格快照：${totals.cost_estimate.rate_dates.join('、')}。` : ''}估算不扣减账户余额。未计价请求 {value(totals.unpriced_requests)} 次。</small>
        </div>
      </> : <>
        <div className="usage-cards">
          <div><small>已报告 Token</small><strong>{value(totals.total_tokens)}</strong><span>输入 {value(totals.input_tokens)} · 输出 {value(totals.output_tokens)}</span></div>
          <div><small>已结束请求</small><strong>{value(totals.requests)}</strong><span>有用量记录 {value(totals.reported_requests)} 次</span></div>
          <div><small>缓存读取 Token</small><strong>{value(totals.cache_read)}</strong><span>属于输入 Token 子集</span></div>
          <div><small>推理 Token</small><strong>{value(totals.reasoning)}</strong><span>属于已报告 Token 子集</span></div>
          <div><small>JEV 请求</small><strong>{value(totals.jev_requests)}</strong><span>仅统计 Token，研值为 0</span></div>
        </div>
        <p className="pref-desc usage-note">仅统计已结束请求；失败或中断且没有用量返回的请求不入账。没有用量记录的请求不纳入已报告 Token 汇总；缓存读取和推理 Token 是子集，不会再次相加。</p>
      </>}
    </section>

    {/* 第二步 & 第三步：按模型和每日表格明细 */}
    {mode === 'credits' ? <>
      <section className="pref-section">
        <h2>按模型研值估算</h2>
        <div className="usage-table-wrap">
          <table className="usage-table">
            <thead>
              <tr><th>模型</th><th>请求数</th><th>预计成本</th></tr>
            </thead>
            <tbody>
              {stats.models.map(item => <tr key={`${item.source}:${item.model}`}>
                <th scope="row">{item.model}{item.source === 'jev' && <small>JEV</small>}</th>
                <td>{value(item.requests)}</td>
                <td>{estimate(item)}</td>
              </tr>)}
              {!stats.models.length && <tr><td colSpan={3}>此筛选下暂无记录 · 预计研值 0</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="pref-section">
        <h2>每日研值估算</h2>
        <div className="usage-table-wrap">
          <table className="usage-table">
            <thead>
              <tr><th>日期</th><th>模型</th><th>请求数</th><th>预计成本</th></tr>
            </thead>
            <tbody>
              {stats.daily.map(item => <tr key={`${item.date}:${item.source}:${item.model}`}>
                <th scope="row">{item.date}</th>
                <td>{item.model}{item.source === 'jev' && <small className="usage-source">JEV</small>}</td>
                <td>{value(item.requests)}</td>
                <td>{estimate(item)}</td>
              </tr>)}
              {!stats.daily.length && <tr><td colSpan={4}>此筛选下暂无记录 · 预计研值 0</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </> : <>
      <section className="pref-section">
        <h2>按模型 Token 统计</h2>
        <div className="usage-table-wrap">
          <table className="usage-table">
            <thead>
              <tr><th>模型</th><th>请求 / 已报告</th><th>输入</th><th>输出</th><th>已报告 Token</th><th>缓存读取</th><th>推理 Token</th></tr>
            </thead>
            <tbody>
              {stats.models.map(item => <tr key={`${item.source}:${item.model}`}>
                <th scope="row">{item.model}{item.source === 'jev' && <small>JEV</small>}</th>
                <td>{value(item.requests)} / {value(item.reported_requests)}</td>
                <td>{value(item.input_tokens)}</td>
                <td>{value(item.output_tokens)}</td>
                <td>{value(item.total_tokens)}</td>
                <td>{value(item.cache_read)}</td>
                <td>{value(item.reasoning)}</td>
              </tr>)}
              {!stats.models.length && <tr><td colSpan={7}>此筛选下暂无记录</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="pref-section">
        <h2>每日 Token 用量</h2>
        <div className="usage-table-wrap">
          <table className="usage-table">
            <thead>
              <tr><th>日期</th><th>模型</th><th>请求 / 已报告</th><th>输入</th><th>输出</th><th>已报告 Token</th><th>缓存读取</th><th>推理 Token</th></tr>
            </thead>
            <tbody>
              {stats.daily.map(item => <tr key={`${item.date}:${item.source}:${item.model}`}>
                <th scope="row">{item.date}</th>
                <td>{item.model}{item.source === 'jev' && <small className="usage-source">JEV</small>}</td>
                <td>{value(item.requests)} / {value(item.reported_requests)}</td>
                <td>{value(item.input_tokens)}</td>
                <td>{value(item.output_tokens)}</td>
                <td>{value(item.total_tokens)}</td>
                <td>{value(item.cache_read)}</td>
                <td>{value(item.reasoning)}</td>
              </tr>)}
              {!stats.daily.length && <tr><td colSpan={8}>此筛选下暂无记录</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </>}
  </>
}

/**
 * 用量统计页面外壳组件。
 * 负责时间周期（年/月/日/全部）及模型筛选状态维护、向后端请求最新统计、并监听任务结束实时刷新。
 *
 * @param props - 模式与当前用户资料
 */
export function UsageSettings({ mode, user }: { mode: UsageMode; user: UserProfile | null }) {
  const [period, setPeriod] = useState(currentMonth)
  const [model, setModel] = useState('')
  const [stats, setStats] = useState<UsageStats | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const requestVersion = useRef(0)
  const [refresh, setRefresh] = useState(0)

  // 周期或模型变更时重新拉取用量数据
  useEffect(() => {
    let active = true
    setStats(null)
    const fetchStats = async () => {
      const version = ++requestVersion.current
      setLoading(true)
      setError('')
      try {
        const result = await window.medpeer.command({
          id: crypto.randomUUID(),
          method: 'usage.stats',
          params: { period, model }
        }) as UsageStats
        if (active && version === requestVersion.current) setStats(result)
      } catch (reason) {
        if (active && version === requestVersion.current) setError(String(reason))
      } finally {
        if (active && version === requestVersion.current) setLoading(false)
      }
    }
    void fetchStats()

    // 监听任务结束事件，自动触发刷新
    const unsubscribe = window.medpeer.onEvent(event => {
      if (event.event === 'run.finished') setRefresh(val => val + 1)
    })
    return () => {
      active = false
      unsubscribe()
    }
  }, [period, model, refresh, user?.userID])

  // 时间下拉候选集解析
  const dates = stats?.dates ?? []
  const years = [...new Set([...dates.map(date => date.slice(0, 4)), currentMonth().slice(0, 4)])].sort().reverse()
  const months = [...new Set([...dates.map(date => date.slice(0, 7)), currentMonth()])].sort().reverse()
  const days = [...new Set([...dates, stats?.today ?? ''])].filter(Boolean).sort().reverse()
  const granularity = period.length === 4 ? 'year' : period.length === 7 ? 'month' : period ? 'day' : 'all'

  /** 切换时间统计粒度 */
  const selectGranularity = (next: string) =>
    setPeriod(next === 'all' ? '' : next === 'year' ? currentMonth().slice(0, 4) : next === 'day' ? stats?.today ?? '' : currentMonth())

  return <>
    <section className="pref-section usage-overview">
      <div className="pref-section-heading">
        <div>
          <h2>本机用量统计</h2>
          <p>当前账户在本机桌面端所有项目的汇总，按北京时间统计。</p>
        </div>
        <div className="usage-refresh">
          <span className="pref-state">{loading ? '更新中…' : '仅本机'}</span>
          <button disabled={loading} onClick={() => setRefresh(val => val + 1)}>刷新</button>
        </div>
      </div>
      <div className="usage-filters">
        <label>
          周期
          <select aria-label="统计周期" value={granularity} onChange={event => selectGranularity(event.target.value)}>
            <option value="all">全部时间</option>
            <option value="year">按年</option>
            <option value="month">按月</option>
            <option value="day">按日</option>
          </select>
        </label>
        {granularity !== 'all' && <label>
          {granularity === 'year' ? '年份' : granularity === 'month' ? '月份' : '日期'}
          <select aria-label="选择日期" value={period} onChange={event => setPeriod(event.target.value)}>
            {(granularity === 'year' ? years : granularity === 'month' ? months : days).map(option => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </label>}
        <label>
          模型
          <select aria-label="筛选模型" value={model} onChange={event => setModel(event.target.value)}>
            <option value="">所有模型</option>
            {stats?.model_names.map(name => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
        {stats?.tracking_since && <small>开始记录：{stats.tracking_since} · 旧历史记录不会补录</small>}
      </div>
      {error && <div className="settings-feedback" role="alert">{error}</div>}
    </section>

    {stats ? (
      <UsageStatsContent mode={mode} stats={stats} user={user}/>
    ) : !loading && !error ? (
      <p className="pref-desc">暂无用量记录。</p>
    ) : null}
  </>
}
