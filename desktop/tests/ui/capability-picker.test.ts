import { expect, test } from 'vitest'
import { isCapabilityShortcut } from '../../src/renderer/src/components/CapabilityPicker'

test('opens the capability menu for an unmodified slash key only', () => {
  expect(isCapabilityShortcut({ key: '/', ctrlKey: false, metaKey: false, altKey: false })).toBe(true)
  expect(isCapabilityShortcut({ key: '/', ctrlKey: true, metaKey: false, altKey: false })).toBe(false)
  expect(isCapabilityShortcut({ key: 'a', ctrlKey: false, metaKey: false, altKey: false })).toBe(false)
})
