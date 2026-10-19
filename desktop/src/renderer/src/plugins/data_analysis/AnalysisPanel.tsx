/**
 * @file AnalysisPanel.tsx
 * @description 科研数据分析侧边详情面板组件。
 * 负责展示当前活跃分析模块名称、已执行的 Python 统计与绘图代码证据，
 * 以及最新生成的分析报告文件路径，并提供一键打开查看报告的操作。
 * @author tongliga
 * @date 2026-10-16
 */

import type { Json } from '../../../../shared/protocol'

/**
 * 数据分析插件运行时状态结构契约。
 */
export type AnalysisState = {
  /** 激活的分析子模块名称 */
  active_plugin?: Json
  /** 分析过程中执行的代码与输出证据列表 */
  analysis_executions?: Json[]
  /** 交付报告在工作区中的相对路径 */
  analysis_report_path?: string
  /** 报告供外部打开的有效路径 */
  report_open_path?: string
}

/**
 * 数据分析详情面板组件。
 *
 * @param props - 组件属性
 */
export function AnalysisPanel({
  state,
  delivered,
  onOpen
}: {
  /** 数据分析插件状态对象 */
  state: AnalysisState | null
  /** 本轮次是否已成功交付新报告 */
  delivered: boolean
  /** 打开交付报告回调函数 */
  onOpen(path: string): void
}) {
  return <section className="side-section">
    <h3>科研分析</h3>
    {!state && <p className="muted">等待科研状态</p>}
    {state && <>
      {'active_plugin' in state && <p>当前模块：{String(state.active_plugin)}</p>}
      {'analysis_executions' in state && (
        <details>
          <summary>执行证据</summary>
          <pre>{JSON.stringify(state.analysis_executions, null, 2)}</pre>
        </details>
      )}
      {state.analysis_report_path && <div className="report">
        <strong>{delivered ? '本轮报告已交付' : '已有报告'}</strong>
        <code>{state.analysis_report_path}</code>
        {state.report_open_path ? (
          <button onClick={() => onOpen(state.report_open_path!)}>打开报告</button>
        ) : (
          <small>当前工作区内不可打开</small>
        )}
      </div>}
    </>}
  </section>
}
