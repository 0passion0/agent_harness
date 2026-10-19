import { afterEach, expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { PluginSlot } from '../../src/renderer/src/components/PluginSlot'
import { pluginViews } from '../../src/renderer/src/plugins'
import type { PluginViewProps } from '../../src/renderer/src/plugins/types'

afterEach(() => { delete pluginViews.notes })

const props = {
  question: null, events: [], saved: false, runId: null,
  answer: () => {}, open: () => {}
}

test('host mounts an independently registered plugin with its unchanged state', () => {
  pluginViews.notes = ({ state }: PluginViewProps) => <p>{String(state!.note)}</p>
  const html = renderToStaticMarkup(<PluginSlot {...props}
    plugins={[{ id: 'notes', name: 'Notes', description: '', interaction_kinds: [] }]}
    states={{ notes: { note: '原始笔记' } }} />)
  expect(html).toContain('原始笔记')
  expect(html).not.toContain('科研分析')
  expect(renderToStaticMarkup(<PluginSlot {...props} plugins={[]} states={{ notes: { note: '原始笔记' } }} />)).toBe('')
})

test('available research plugin does not activate on empty checkpoint fields', () => {
  const html = renderToStaticMarkup(<PluginSlot {...props}
    plugins={[{ id: 'data_analysis', name: '科研', description: '', interaction_kinds: ['research_question'] }]}
    states={{ data_analysis: { analysis_executions: [], analysis_report_path: '' } }} />)
  expect(html).toBe('')
})
