import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { isItemVisible, WorkspaceDirectory } from '../../src/renderer/src/components/WorkspaceDirectory'
import type { WorkspaceFileEntry } from '../../src/shared/protocol'

describe('WorkspaceDirectory component', () => {
  it('renders guidance and action button when workspace is not selected', () => {
    const chooseWorkspaceMock = vi.fn()
    const html = renderToStaticMarkup(
      <WorkspaceDirectory workspace="" onChooseWorkspace={chooseWorkspaceMock} />
    )
    expect(html).toContain('未关联工作目录')
    expect(html).toContain('选择本地工作区')
    expect(html).toContain('对话中生成的报告、数据与引用文件将直接在此查看、打开和拖拽')
  })

  it('renders workspace directory header and search input when workspace is provided', () => {
    const html = renderToStaticMarkup(
      <WorkspaceDirectory workspace="D:/project/my-research" onChooseWorkspace={() => {}} />
    )
    expect(html).toContain('my-research')
    expect(html).toContain('D:/project/my-research')
    expect(html).toContain('搜索当前目录文件')
    expect(html).toContain('点击文件夹折叠/展开 · 文件可拖拉')
    expect(html).toContain('在系统文件管理器中打开此目录')
  })

  it('correctly filters tree visibility using isItemVisible for collapsible folders', () => {
    const rootDir: WorkspaceFileEntry = { name: 'src', path: '/w/src', relativePath: 'src', isDirectory: true, size: 0, mtime: 1 }
    const rootFile: WorkspaceFileEntry = { name: 'README.md', path: '/w/README.md', relativePath: 'README.md', isDirectory: false, size: 10, mtime: 1 }
    const subFile: WorkspaceFileEntry = { name: 'index.ts', path: '/w/src/index.ts', relativePath: 'src/index.ts', isDirectory: false, size: 20, mtime: 1 }
    const deepDir: WorkspaceFileEntry = { name: 'components', path: '/w/src/components', relativePath: 'src/components', isDirectory: true, size: 0, mtime: 1 }
    const deepFile: WorkspaceFileEntry = { name: 'App.tsx', path: '/w/src/components/App.tsx', relativePath: 'src/components/App.tsx', isDirectory: false, size: 30, mtime: 1 }

    // 默认全收起状态：仅根目录项可见
    const emptyExpanded = new Set<string>()
    expect(isItemVisible(rootDir, emptyExpanded)).toBe(true)
    expect(isItemVisible(rootFile, emptyExpanded)).toBe(true)
    expect(isItemVisible(subFile, emptyExpanded)).toBe(false)
    expect(isItemVisible(deepDir, emptyExpanded)).toBe(false)
    expect(isItemVisible(deepFile, emptyExpanded)).toBe(false)

    // 展开 src 目录：src 下的一级子项可见，但更深层仍隐藏
    const expandedSrc = new Set(['src'])
    expect(isItemVisible(subFile, expandedSrc)).toBe(true)
    expect(isItemVisible(deepDir, expandedSrc)).toBe(true)
    expect(isItemVisible(deepFile, expandedSrc)).toBe(false)

    // 同时展开 src 与 src/components：深层文件可见
    const expandedDeep = new Set(['src', 'src/components'])
    expect(isItemVisible(deepFile, expandedDeep)).toBe(true)
  })
})
