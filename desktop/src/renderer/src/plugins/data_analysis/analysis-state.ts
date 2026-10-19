/**
 * @file analysis-state.ts
 * @description 科研数据分析插件状态判定与交付状态计算纯函数。
 * 负责评估当前会话是否包含有效的数据分析上下文，以及检测本轮任务中是否交付了最终分析报告。
 * @author tongliga
 * @date 2026-10-16
 */

import type { RunRecord } from '../../../../shared/protocol'
import type { AnalysisState } from './AnalysisPanel'

/**
 * 判断当前会话或快照中是否具有科研数据分析的相关上下文。
 *
 * @param state - 数据分析插件状态对象
 * @returns 若激活了分析插件、存在执行证据或已有报告路径，则返回 true
 */
export function hasAnalysisContext(state: AnalysisState | null): boolean {
  return (
    state?.active_plugin === 'data_analysis' ||
    Boolean(state?.analysis_executions?.length || state?.analysis_report_path)
  )
}

/**
 * 判断本轮执行是否成功产出并交付了科研报告。
 *
 * @param events - 本轮任务产生的事件流水列表
 * @param saved - 本轮任务是否已持久化保存
 * @returns 若已保存且成功调用了 finalize_report 工具，则返回 true
 */
export function deliveredThisRun(events: RunRecord[], saved: boolean): boolean {
  return (
    saved &&
    events.some(event => event.kind === 'tool_end' && event.name === 'finalize_report' && event.status === 'success')
  )
}
