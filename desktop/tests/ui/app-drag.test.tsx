import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
  application: { user: {}, active_project_id: 'project', projects: [], model: null },
  project: { name: 'project' }, workspace: '', draft: '', setDraft: vi.fn(),
  attachments: [], setAttachments: vi.fn(), sessions: [], sessionSearch: '',
  searchSessions: vi.fn(), plugins: [], pluginStates: {}, config: {}, error: '',
  clearError: vi.fn(), busy: false, chooseWorkspace: vi.fn(), loadSession: vi.fn(),
  send: vi.fn(), stop: vi.fn(), answer: vi.fn(), openArtifact: vi.fn(),
  importDroppedFiles: vi.fn(), reportError: vi.fn()
}))

vi.mock('../../src/renderer/src/state/use-agent-controller', () => ({
  useAgentController: () => controller
}))

vi.mock('../../src/renderer/src/components/Conversation', () => ({ Conversation: () => null }))
vi.mock('../../src/renderer/src/components/SessionSidebar', () => ({ SessionSidebar: () => null }))
vi.mock('../../src/renderer/src/components/PluginSlot', () => ({ PluginSlot: () => null }))
vi.mock('../../src/renderer/src/components/RunDetails', () => ({ RunDetails: () => null }))
vi.mock('../../src/renderer/src/components/ToolCard', () => ({ ToolCard: () => null }))
vi.mock('../../src/renderer/src/components/AttachmentChips', () => ({ AttachmentChips: () => null }))
vi.mock('../../src/renderer/src/components/TokenWindowPopup', () => ({ TokenWindowPopup: () => null }))
vi.mock('../../src/renderer/src/features/auth/LoginScreen', () => ({ LoginScreen: () => null }))
vi.mock('../../src/renderer/src/features/auth/AccountMenu', () => ({ AccountMenu: () => null }))

import App from '../../src/renderer/src/App'
import { WorkspaceDirectory } from '../../src/renderer/src/components/WorkspaceDirectory'

function appRoot() {
  hooks.index = 0
  hooks.setters = []
  return App() as React.ReactElement<{ [key: string]: (event: DragEventLike) => void }>
}

interface DragEventLike {
  target: { closest(selector: string): Element | null }
  dataTransfer: { types: string[]; files: File[]; dropEffect: string }
  preventDefault(): void
  stopPropagation(): void
}

function drag(target: 'chat' | 'directory' | 'elsewhere' = 'chat', types = ['Files']): DragEventLike {
  return {
    target: { closest: selector => selector === '.task-main' && target === 'chat' ? {} as Element :
      selector === '.workspace-dir-panel' && target === 'directory' ? {} as Element : null },
    dataTransfer: { types, files: [new File(['x'], 'x.txt')], dropEffect: 'none' },
    preventDefault: vi.fn(), stopPropagation: vi.fn()
  }
}

describe('app file drag handling', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    hooks.index = 0
    hooks.setters = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('tracks nested directory enter and leave without opening the chat overlay', () => {
    const root = appRoot()
    const directory = drag('directory')
    const panel = WorkspaceDirectory({ workspace: 'D:/workspace', onChooseWorkspace: vi.fn() }) as React.ReactElement<{
      onDragEnterCapture(event: DragEventLike): void
      onDragLeaveCapture(event: DragEventLike): void
    }>
    root.props.onDragEnterCapture(directory)
    panel.props.onDragEnterCapture(directory)
    panel.props.onDragEnterCapture(directory)
    expect(hooks.setters[6]).toHaveBeenLastCalledWith(false)
    panel.props.onDragLeaveCapture(directory)
    expect(hooks.setters[11]).toHaveBeenLastCalledWith(true)
    panel.props.onDragLeaveCapture(directory)
    expect(hooks.setters[11]).toHaveBeenLastCalledWith(false)
    expect(hooks.setters[6]).toHaveBeenLastCalledWith(false)
  })

  it('shows chat overlay again when crossing from directory back into chat', () => {
    const root = appRoot()
    root.props.onDragEnterCapture(drag('elsewhere'))
    root.props.onDragEnterCapture(drag('directory'))
    root.props.onDragLeaveCapture(drag('directory'))
    root.props.onDragEnterCapture(drag('chat'))
    expect(hooks.setters[6]).toHaveBeenLastCalledWith(true)
  })

  it('contains a directory drop and lets the next chat drop import once', async () => {
    vi.useFakeTimers()
    const copy = vi.fn().mockResolvedValue(['D:/workspace/x.txt'])
    vi.stubGlobal('window', { medpeer: {
      copyDroppedFilesToWorkspace: copy,
      listWorkspaceDirectory: vi.fn().mockResolvedValue([])
    } })
    const root = appRoot()
    const panel = WorkspaceDirectory({ workspace: 'D:/workspace', onChooseWorkspace: vi.fn() }) as React.ReactElement<{
      onDrop(event: DragEventLike): Promise<void>
      onDragEnterCapture(event: DragEventLike): void
      onDragOver(event: DragEventLike): void
    }>
    const directoryDrop = drag('directory')
    root.props.onDragEnterCapture(drag('elsewhere'))
    root.props.onDragEnterCapture(directoryDrop)
    root.props.onDragEnterCapture(directoryDrop)
    root.props.onDragOverCapture(drag('chat'))
    expect(hooks.setters[6]).toHaveBeenLastCalledWith(true)
    root.props.onDragOverCapture(directoryDrop)
    expect(hooks.setters[6]).toHaveBeenLastCalledWith(false)
    panel.props.onDragOver(directoryDrop)
    expect(directoryDrop.dataTransfer.dropEffect).toBe('copy')
    vi.mocked(directoryDrop.stopPropagation).mockClear()
    hooks.setters[6].mockClear()
    root.props.onDropCapture(directoryDrop)
    await panel.props.onDrop(directoryDrop)
    expect(copy).toHaveBeenCalledExactlyOnceWith(directoryDrop.dataTransfer.files, 'D:/workspace')
    expect(directoryDrop.stopPropagation).toHaveBeenCalledOnce()
    expect(hooks.setters[6]).toHaveBeenLastCalledWith(false)
    expect(controller.importDroppedFiles).not.toHaveBeenCalled()
    root.props.onDrop(drag('elsewhere'))
    expect(controller.importDroppedFiles).not.toHaveBeenCalled()

    const textDrag = drag('chat', ['text/plain'])
    hooks.setters[11].mockClear()
    panel.props.onDragEnterCapture(textDrag)
    panel.props.onDragOver(textDrag)
    await panel.props.onDrop(textDrag)
    root.props.onDragEnterCapture(textDrag)
    root.props.onDropCapture(textDrag)
    root.props.onDrop(textDrag)
    expect(hooks.setters[11]).not.toHaveBeenCalled()
    expect(copy).toHaveBeenCalledExactlyOnceWith(directoryDrop.dataTransfer.files, 'D:/workspace')
    expect(controller.importDroppedFiles).not.toHaveBeenCalled()

    const chatDrop = drag('chat')
    root.props.onDragEnterCapture(chatDrop)
    root.props.onDragLeaveCapture(chatDrop)
    expect(hooks.setters[6]).toHaveBeenLastCalledWith(false)
    root.props.onDragEnterCapture(chatDrop)
    root.props.onDropCapture(chatDrop)
    root.props.onDrop(chatDrop)
    expect(controller.importDroppedFiles).toHaveBeenCalledExactlyOnceWith(chatDrop.dataTransfer.files)
  })
})
