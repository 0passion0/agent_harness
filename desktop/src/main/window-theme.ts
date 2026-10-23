import type { TitleBarOverlay } from 'electron'

export function titleBarOverlay(theme: unknown): Required<TitleBarOverlay> {
  if (theme !== 'light' && theme !== 'dark') throw new Error('窗口主题无效')
  return {
    color: theme === 'dark' ? '#191a2f' : '#f3f3fb',
    symbolColor: theme === 'dark' ? '#d4d1eb' : '#3a3c54',
    height: 36
  }
}
