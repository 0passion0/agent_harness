import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { HarnessSettings, harnessProjectLabel } from '../../src/renderer/src/plugins/user_config/HarnessSettings'

describe('HarnessSettings', () => {
  it('renders the global instruction scope without a generic project option', () => {
    const html = renderToStaticMarkup(<HarnessSettings kind="agent" application={null} busy={false} />)

    expect(html).toContain('Agent 指令')
    expect(html).toContain('系统 ~/.agent-harness')
    expect(html).not.toContain('当前项目')
  })

  it('offers ZIP import on the separate skills tab', () => {
    const html = renderToStaticMarkup(<HarnessSettings kind="skills" application={null} busy={false} />)

    expect(html).toContain('技能')
    expect(html).toContain('导入 ZIP 技能')
    expect(html).toContain('所选项目目录')
  })

  it('labels project choices with the real directory basename and full path', () => {
    expect(harnessProjectLabel({ id: 'p1', name: 'my-app', path: 'D:\\work\\my-app' }))
      .toBe('my-app — D:\\work\\my-app')
  })
})
