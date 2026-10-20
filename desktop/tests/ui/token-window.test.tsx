import { expect, test, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { SessionTokenReport, TokenSectionUsage } from '../../src/shared/protocol'
import { TokenWindowPopup } from '../../src/renderer/src/components/TokenWindowPopup'

vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState: () => [true, () => {}]
}))

test('context popup shows unknown sections as unmeasured instead of zero', () => {
  const known: TokenSectionUsage = { name: '历史对话', tokens: 1500, bytes: 6000, ratio_of_used: 1, ratio_of_max: 0.1, description: '' }
  const unknown: TokenSectionUsage = { name: 'MCP 及工具', available: false, tokens: null, bytes: null, ratio_of_used: null, ratio_of_max: null, description: '' }
  const report: SessionTokenReport = {
    max_tokens: 1000, used_tokens: 100, remaining_tokens: 900, used_ratio: 0.1, remaining_ratio: 0.9,
    sections: { system_prompt: known, history: known, tools: unknown }, summary_text: '最近一次请求；各项为估算。'
  }
  const html = renderToStaticMarkup(<TokenWindowPopup report={report} />)
  expect(html).toContain('未统计')
  expect(html).toContain('100')
  expect(html).not.toContain('NaN')
  expect(html).toContain(report.summary_text)
  expect(html).toContain('1.5k')
})
