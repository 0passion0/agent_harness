/**
 * @file index.tsx
 * @description 科研数据分析插件主入口组件。
 * 组合呈现分析证据面板（AnalysisPanel）与交互式科研提问组件（ResearchQuestion）。
 * @author tongliga
 * @date 2026-10-16
 */

import type { PluginViewProps } from '../types'
import { ResearchQuestion } from './ResearchQuestion'
import { AnalysisPanel, type AnalysisState } from './AnalysisPanel'
import { deliveredThisRun, hasAnalysisContext } from './analysis-state'

/**
 * 科研数据分析插件插槽主视图。
 *
 * @param props - 插件标准视图属性
 */
export function DataAnalysis({ state: snapshot, question, events, saved, runId, answer, open }: PluginViewProps) {
  const state = snapshot as AnalysisState | null
  const showPanel = hasAnalysisContext(state)

  return <>
    {showPanel && <AnalysisPanel state={state} delivered={deliveredThisRun(events, saved)} onOpen={open} />}
    {question?.plugin_id === 'data_analysis' && question.kind === 'research_question' && runId && (
      <ResearchQuestion
        key={question.interaction_id}
        question={question}
        disabled={false}
        onAnswer={text => answer(runId, question.interaction_id, text)}
      />
    )}
  </>
}
