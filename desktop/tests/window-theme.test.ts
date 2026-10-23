import { expect, test } from 'vitest'
import { titleBarOverlay } from '../src/main/window-theme'

test('native caption buttons match both themes and keep the draggable strip height', () => {
  expect(titleBarOverlay('light')).toEqual({ color: '#f3f3fb', symbolColor: '#3a3c54', height: 36 })
  expect(titleBarOverlay('dark')).toEqual({ color: '#191a2f', symbolColor: '#d4d1eb', height: 36 })
})

test('rejects invalid themes at the IPC boundary', () => {
  for (const theme of [undefined, null, '', 'system', {}, 1]) {
    expect(() => titleBarOverlay(theme)).toThrow('窗口主题无效')
  }
})
