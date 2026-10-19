import { describe, expect, it, vi } from 'vitest'
import { Children, isValidElement } from 'react'
import type { ComponentProps, ReactElement } from 'react'
import { SessionSidebar } from '../../src/renderer/src/components/SessionSidebar'

vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState: (initial: unknown) => [initial, vi.fn()]
}))

function sidebar(overrides: Partial<ComponentProps<typeof SessionSidebar>> = {}) {
  const props: ComponentProps<typeof SessionSidebar> = {
    sessions: [], search: '', onSearch: vi.fn(), projectId: null, projects: [],
    busy: false, connected: true, onChooseWorkspace: vi.fn(), onSelectProject: vi.fn(),
    onResume: vi.fn(), onClear: vi.fn(), onRename: vi.fn(),
    onDelete: vi.fn(), onExport: vi.fn(), onOpenSettings: vi.fn(), theme: 'light',
    onToggleTheme: vi.fn(), ...overrides
  }
  const root = SessionSidebar(props)
  const add = Children.toArray(root.props.children).find(child =>
    isValidElement<{ className?: string }>(child) && child.props.className === 'project-add'
  ) as ReactElement<{ onClick(): void; disabled: boolean }>
  return { props, add }
}

describe('sidebar project creation', () => {
  it.each([false, true])('opens the folder picker directly with an existing project: %s', existing => {
    const { props, add } = sidebar(existing ? {
      projectId: 'existing',
      projects: [{ id: 'existing', name: 'Existing', path: 'D:/existing' }]
    } : {})

    add.props.onClick()

    expect(props.onChooseWorkspace).toHaveBeenCalledOnce()
    expect(props.onSelectProject).not.toHaveBeenCalled()
  })

  it('disables project creation while a task is running', () => {
    const { add } = sidebar({ busy: true })
    expect(add.props.disabled).toBe(true)
  })
})
