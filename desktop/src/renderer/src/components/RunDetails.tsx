/**
 * @file RunDetails.tsx
 * @description 任务运行明细与执行日志展示组件。
 * 负责呈现会话状态、Token 消耗统计、研值及费用区间估算、
 * 本轮实时事件调用轨迹（包含模型输入输出、耗时）以及历史已保存轮次记录。
 * @author tongliga
 * @date 2026-10-16
 */

import type { AgentState } from '../state/agent-state'
import type { CostEstimate, Json, RunRecord, SessionModelUsage } from '../../../shared/protocol'
import { ToolCard } from './ToolCard'

/**
 * 运行时事件类别到人类易读中文标签的映射表。
 */
const eventLabels: Record<string, string> = {
  model_start: '模型请求开始',
  model_end: '模型请求完成',
  model_error: '模型请求失败',
  tool_proposed: '模型提出工具调用',
  tool_start: '工具开始执行',
  tool_end: '工具执行结束',
  task_end: '本轮统计',
  run_finished: '本轮结束并保存'
}

/**
 * 运行明细与审计日志视图组件。
 *
 * 第一步：提取累计统计（liveTotals 或 snapshot.totals）、研值估算及模型明细。
 * 第二步：展示任务当前生命周期状态与 Token 累计消耗。
 * 第三步：展示费用与研值估算详情（说明非 JEV 模型计费规则与峰谷价格区间）。
 * 第四步：逐项展开本轮事件日志（包含模型参数、输入上下文及输出）。
 * 第五步：展示历史已保存的执行轮次及对应的工具调用卡片。
 *
 * @param props - 包含当前智能体状态树的对象
 */
export function RunDetails({ state }: { state: AgentState }) {
  // 第一步：提取汇总用量与估算数据
  const totals = state.liveTotals ?? state.snapshot?.totals
  const cost = totals?.cost_estimate as CostEstimate | null | undefined
  const models = (totals?.models ?? []) as SessionModelUsage[]
  const latest = state.snapshot?.runs.at(-1)
  const runLog = state.runLog.filter(record => record.kind !== 'text')

  return <section className="side-section">
    <h3>运行明细</h3>
    {/* 第二步：执行状态描述 */}
    <p>
      状态：{state.phase === 'idle'
        ? latest || state.lastRunSaved ? '已保存' : '未开始'
        : state.phase === 'disconnected' ? '未连接'
        : state.phase === 'running' ? '运行中'
        : state.phase === 'question' ? '等待答复'
        : '正在停止'}
    </p>
    <p>
      会话累计 Token：{totals ? String(totals.total_tokens) : '暂无用量'}
      {totals && !totals.complete && '（用量未完整返回）'}
    </p>

    {/* 第三步：费用与研值估算面板 */}
    {cost ? (
      <p>已计价部分预计消耗：最多约 {Number(cost.maximum_credits).toFixed(2)} 研值（¥{Number(cost.maximum_cny).toFixed(2)}）</p>
    ) : totals?.requests && totals.jev_requests === totals.requests ? (
      <p>预计消耗：0 研值（本会话仅有 JEV 请求）。</p>
    ) : (
      <p>暂无可计价用量；用量或历史估算未提供。</p>
    )}

    {(cost || models.length > 0) && <details className="usage-detail">
      <summary>查看用量与费用明细</summary>
      {cost && <>
        <p>已计价部分预计消耗：{cost.minimum_credits}–{cost.maximum_credits} 研值（¥{cost.minimum_cny}–¥{cost.maximum_cny}）</p>
        <p>所有非 JEV 模型按统一费率成本加价 50%，20 元 = 2000 研值；小数累计，不逐次取整。仅展示，未实际扣费。</p>
        <p>区间包含峰谷价差及缺失缓存明细的影响：下限按输入全部命中缓存、谷时价，上限按输入全部未命中缓存、峰时价；费率日期：{cost.rate_dates.join('、')}。</p>
      </>}
      {models.length > 0 && <ul className="usage-models">
        {models.map(item => <li key={item.model}>
          <strong>{item.model}</strong>：Token {item.total_tokens.toLocaleString('zh-CN')}（输入 {item.input_tokens.toLocaleString('zh-CN')} · 输出 {item.output_tokens.toLocaleString('zh-CN')} · 缓存命中 {item.cache_hit_tokens == null ? '未返回' : item.cache_hit_tokens.toLocaleString('zh-CN')}）· {item.requests} 次请求
          · {item.cost_estimate ? `${item.cost_estimate.minimum_credits}–${item.cost_estimate.maximum_credits} 研值（¥${item.cost_estimate.minimum_cny}–¥${item.cost_estimate.maximum_cny}）` : item.jev_requests === item.requests ? '0 研值（JEV）' : '未计价'}
          {item.cost_estimate && item.unpriced_requests > 0 && ` · 另有 ${item.unpriced_requests} 次未计价`}
        </li>)}
      </ul>}
    </details>}

    {!!totals?.jev_requests && <p>JEV 请求仅统计 Token，不计研值。</p>}
    {!!totals?.unpriced_requests && <p>另有 {String(totals.unpriced_requests)} 次请求未计价（含缺少估算的历史请求及用量未返回的请求），未包含在估算中。</p>}
    {state.error && <p role="alert">{state.error}</p>}
    {state.question && <p>等待答复：{state.question.question}</p>}

    {/* 第四步：本轮实时事件流水日志 */}
    <h3>本轮事件日志 · {runLog.length}</h3>
    {runLog.map((record, index) => <details className="run-log-entry" key={index}>
      <summary>
        {index + 1}. {eventLabels[record.kind] ?? record.kind}
        {record.name && <> · {record.name}</>}
        {record.model && <> · {String(record.model)}</>}
        {record.status && <> · {record.status}</>}
        {record.elapsed != null && <> · {(record.elapsed as number).toFixed(2)} 秒</>}
      </summary>
      {record.input !== undefined && <>
        <h4>模型输入</h4>
        <pre>{JSON.stringify(record.input, null, 2)}</pre>
        <h4>模型输出</h4>
        {record.output === null ? <p>未返回完整模型输出。</p> : <pre>{JSON.stringify(record.output, null, 2)}</pre>}
      </>}
      <details>
        <summary>完整事件记录</summary>
        <pre>{JSON.stringify(record, null, 2)}</pre>
      </details>
    </details>)}
    {!runLog.length && <p>开始任务后显示模型请求、工具调用、参数、结果、用量和错误。已保存的记录见下方。</p>}

    {/* 第五步：历史已持久化轮次执行记录 */}
    <h3>已保存运行 · {state.snapshot?.runs.length ?? 0}</h3>
    {state.snapshot?.runs.map((run, index) => <details className="run-log-entry" key={index}>
      <summary>第 {index + 1} 轮 · {run.elapsed.toFixed(2)} 秒 · {run.requests.length} 次模型请求 · {Object.keys(run.tools).length} 次工具调用</summary>
      <h4>模型请求</h4>
      {run.requests.map((request, requestIndex) => {
        const record = request as { [key: string]: Json }
        return <details key={requestIndex}>
          <summary>请求 {requestIndex + 1} · {String(record.model ?? record.name)} · {String(record.status ?? record.kind)}</summary>
          {record.input !== undefined && <>
            <h4>模型输入</h4>
            <pre>{JSON.stringify(record.input, null, 2)}</pre>
            <h4>模型输出</h4>
            {record.output === null ? <p>未返回完整模型输出。</p> : <pre>{JSON.stringify(record.output, null, 2)}</pre>}
          </>}
          <details>
            <summary>完整请求记录</summary>
            <pre>{JSON.stringify(request, null, 2)}</pre>
          </details>
        </details>
      })}
      <h4>工具执行记录</h4>
      {Object.entries(run.tools).map(([id, record]) => (
        <ToolCard key={id} callId={id} record={record as RunRecord} />
      ))}
      <details>
        <summary>完整运行记录</summary>
        <pre>{JSON.stringify(run, null, 2)}</pre>
      </details>
    </details>)}
  </section>
}
