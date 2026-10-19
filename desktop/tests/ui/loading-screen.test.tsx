import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { LoadingScreen } from '../../src/renderer/src/features/boot/LoadingScreen'

describe('LoadingScreen component with Quirky Mascot', () => {
  it('renders minimalist visual loading UI with animated quirky mascot and progress bar without verbose text', () => {
    const html = renderToStaticMarkup(<LoadingScreen theme="light" />)
    expect(html).toContain('quirky-mascot-box')
    expect(html).toContain('quirky-mascot-svg')
    expect(html).toContain('mascot-blob-root')
    expect(html).toContain('noodle-arm')
    expect(html).toContain('boot-progress-bar')
    // 确保没有冗长的生硬文字提示
    expect(html).not.toContain('正在连接本地运行时与服务桥接')
  })

  it('renders dark theme class when theme is dark', () => {
    const html = renderToStaticMarkup(<LoadingScreen theme="dark" />)
    expect(html).toContain('product-v3 dark')
  })

  it('renders error diagnostic UI and switches mascot to error mode when error prop is provided', () => {
    const onRetry = vi.fn()
    const html = renderToStaticMarkup(
      <LoadingScreen
        theme="light"
        error="Python bridge failed to bind port"
        onRetry={onRetry}
      />
    )
    expect(html).toContain('is-error')
    expect(html).toContain('服务未能成功连接')
    expect(html).toContain('Python bridge failed to bind port')
    expect(html).toContain('重新连接')
    expect(html).toContain('查看日志')
  })
})
