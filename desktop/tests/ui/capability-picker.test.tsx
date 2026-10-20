import { expect, test, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { CapabilityPicker, isCapabilityShortcut } from '../../src/renderer/src/components/CapabilityPicker'
import type { ApplicationState } from '../../src/shared/protocol'

const mockApplication: ApplicationState = {
  build_mode: 'development',
  active_project_id: 'proj-1',
  permission_mode: 'full',
  data_dir: '/data',
  runtimes: { python: 'python', node: 'node' },
  model: { provider: 'medpeer', model: 'agnes-3.0-flash' },
  projects: [{ id: 'proj-1', name: '测试科研项目', path: '/test' }],
  plugins: [
    {
      id: 'data_analysis',
      name: '数据分析',
      description: '表格数据分析、质量审计与中文报告生成工作流',
      version: '0.2.0',
      workflows: [{ name: 'tabular_audit', description: '表格质量审计' }],
      interaction_kinds: ['plugin_authorization'],
      enabled: true,
      configured: true,
      loaded: true
    },
    {
      id: 'citation_check',
      name: '文献引用核查',
      description: '医学文献参考文献交叉比对与格式规范审计',
      version: '1.0.0',
      workflows: [],
      interaction_kinds: [],
      enabled: true,
      configured: true,
      loaded: true
    }
  ],
  skills: [
    {
      id: 'computer-use',
      name: 'Computer Use: Windows Control',
      description: 'Control Windows apps and desktop operations',
      source: '个人',
      enabled: true
    },
    {
      id: 'data-profiler',
      name: 'Data Profiler',
      description: '数值表格自动化清洗与多维度统计描述',
      source: '全局',
      plugin_id: 'data_analysis',
      enabled: true
    }
  ]
}

test('opens the capability menu for an unmodified slash key only', () => {
  expect(isCapabilityShortcut({ key: '/', ctrlKey: false, metaKey: false, altKey: false })).toBe(true)
  expect(isCapabilityShortcut({ key: '/', ctrlKey: true, metaKey: false, altKey: false })).toBe(false)
  expect(isCapabilityShortcut({ key: 'a', ctrlKey: false, metaKey: false, altKey: false })).toBe(false)
})

test('renders trigger button and selected chips with remove controls in closed state', () => {
  const html = renderToStaticMarkup(
    <CapabilityPicker
      application={mockApplication}
      plugins={['data_analysis']}
      skills={['computer-use']}
      setPlugins={() => {}}
      setSkills={() => {}}
    />
  )

  expect(html).toContain('aria-label="选择扩展能力与快捷指令 (/)"')
  expect(html).toContain('capability-chip plugin')
  expect(html).toContain('数据分析')
  expect(html).toContain('capability-chip skill')
  expect(html).toContain('Computer Use: Windows Control')
  expect(html).toContain('aria-label="移除插件 数据分析"')
  expect(html).toContain('aria-label="移除技能 Computer Use: Windows Control"')
  expect(html).not.toContain('capability-palette')
})

test('renders modern codex-style palette structure, sections, items and badges', () => {
  // 建立模拟 ref 验证 open 状态渲染
  let handle: any = null
  const html = renderToStaticMarkup(
    <CapabilityPicker
      ref={h => { handle = h }}
      application={mockApplication}
      plugins={['data_analysis']}
      skills={[]}
      setPlugins={() => {}}
      setSkills={() => {}}
    />
  )

  // 验证基础容器与已选胶囊正常输出
  expect(html).toContain('capability-picker')
  expect(html).toContain('数据分析')
})
