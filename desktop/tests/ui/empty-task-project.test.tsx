import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ReactElement } from 'react'

const hooks = vi.hoisted(() => ({
  index: 0,
  setters: [] as Array<ReturnType<typeof vi.fn>>
}))

vi.mock('react', async importOriginal => {
  const actual = await importOriginal<typeof import('react')>()
  return {
    ...actual,
    useEffect: vi.fn(),
    useMemo: (fn: () => unknown) => fn(),
    useRef: (initial: unknown) => ({ current: initial }),
    useState: (initial: unknown) => {
      const value = typeof initial === 'function' ? (initial as () => unknown)() : initial
      const setter = vi.fn()
      hooks.setters[hooks.index++] = setter
      return [value, setter]
    }
  }
})

const controller = vi.hoisted(() => ({
  state: {
    phase: 'idle', snapshot: null, tools: {}, pendingPrompt: null, stream: [],
    question: null, runId: null, lastRunSaved: false, error: null, events: []
  },
  application: { user: { name: 'Dev' }, active_project_id: null, projects: [] as any[], model: 'test' },
  project: null as any,
  workspace: '', draft: '', setDraft: vi.fn(),
  attachments: [], setAttachments: vi.fn(), sessions: [], sessionSearch: '',
  searchSessions: vi.fn(), plugins: [], pluginStates: {}, config: {}, error: '',
  clearError: vi.fn(), busy: false, chooseWorkspace: vi.fn(), loadSession: vi.fn(),
  send: vi.fn(), stop: vi.fn(), answer: vi.fn(), openArtifact: vi.fn(),
  importDroppedFiles: vi.fn(), reportError: vi.fn()
}))

vi.mock('../../src/renderer/src/state/use-agent-controller', () => ({
  useAgentController: () => controller
}))

vi.mock('../../src/renderer/src/components/Conversation', () => ({ Conversation: () => <div data-testid="conversation" /> }))
vi.mock('../../src/renderer/src/components/SessionSidebar', () => ({ SessionSidebar: () => null }))
vi.mock('../../src/renderer/src/components/CapabilityPicker', () => ({ CapabilityPicker: () => null }))
vi.mock('../../src/renderer/src/components/RunDetails', () => ({ RunDetails: () => null }))
vi.mock('../../src/renderer/src/components/ToolCard', () => ({ ToolCard: () => null }))
vi.mock('../../src/renderer/src/components/AttachmentChips', () => ({ AttachmentChips: () => null }))
vi.mock('../../src/renderer/src/components/TokenWindowPopup', () => ({ TokenWindowPopup: () => null }))
vi.mock('../../src/renderer/src/features/auth/LoginScreen', () => ({ LoginScreen: () => null }))
vi.mock('../../src/renderer/src/features/auth/AccountMenu', () => ({ AccountMenu: () => null }))
vi.mock('../../src/renderer/src/components/WorkspaceDirectory', () => ({ WorkspaceDirectory: () => null }))
vi.mock('../../src/renderer/src/plugins/user_config', () => ({ UserConfig: () => null }))

import App from '../../src/renderer/src/App'

function findElement(element: any, predicate: (el: any) => boolean): any {
  if (!element || typeof element !== 'object') return null
  if (predicate(element)) return element
  const children = element.props?.children
  if (Array.isArray(children)) {
    for (const child of children) {
      const found = findElement(child, predicate)
      if (found) return found
    }
  } else if (children) {
    return findElement(children, predicate)
  }
  return null
}

describe('Empty project state in chat window', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    hooks.index = 0
    hooks.setters = []
    controller.application.projects = []
    controller.project = null
    controller.busy = false
  })

  it('renders "新建项目" button and calls chooseWorkspace when there are no projects', () => {
    controller.application.projects = []
    controller.project = null

    const root = App() as ReactElement
    const html = renderToStaticMarkup(root)

    expect(html).toContain('新建项目')
    expect(html).toContain('先新建项目…')
    expect(html).not.toContain('选择一个项目')

    const button = findElement(root, el => el.props?.className?.includes('empty-cta'))
    expect(button).toBeDefined()
    expect(button.props.disabled).toBe(false)

    button.props.onClick()
    expect(controller.chooseWorkspace).toHaveBeenCalledOnce()
  })

  it('renders "选择项目" button when there are existing projects but none selected', () => {
    controller.application.projects = [{ id: 'p1', name: 'Project 1', path: '/path/1' }]
    controller.project = null

    const root = App() as ReactElement
    const html = renderToStaticMarkup(root)

    expect(html).toContain('选择一个项目')
    expect(html).toContain('先选择项目…')
    expect(html).toContain('选择项目')

    const button = findElement(root, el => el.props?.className?.includes('empty-cta'))
    expect(button).toBeDefined()

    // 点击选择项目展开侧边栏（setter index 1 是 sidebarOpen）
    button.props.onClick()
    expect(hooks.setters[1]).toHaveBeenCalledWith(true)
  })
})
