import { describe, expect, it } from 'vitest'
import { deliveredThisRun, hasAnalysisContext } from '../../src/renderer/src/plugins/data_analysis/analysis-state'

describe('research delivery', () => {
  it('requires saved finish and successful finalize_report in this run', () => {
    const events = [{ kind: 'tool_end', call_id: 'a', name: 'finalize_report', status: 'success' }]
    expect(deliveredThisRun(events, false)).toBe(false)
    expect(deliveredThisRun(events, true)).toBe(true)
    expect(deliveredThisRun([{ kind: 'task_end' }], true)).toBe(false)
  })

  it('does not activate research for empty native checkpoint fields', () => {
    expect(hasAnalysisContext({ active_plugin: null, analysis_executions: [], analysis_report_path: '' })).toBe(false)
    expect(hasAnalysisContext({ active_plugin: 'data_analysis', analysis_executions: [], analysis_report_path: '' })).toBe(true)
    expect(hasAnalysisContext({ analysis_executions: [{ tool: 'run_python' }], analysis_report_path: '' })).toBe(true)
    expect(hasAnalysisContext({ analysis_executions: [], analysis_report_path: '/workspace/report.md' })).toBe(true)
  })
})
