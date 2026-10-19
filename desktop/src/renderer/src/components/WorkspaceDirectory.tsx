/**
 * @file WorkspaceDirectory.tsx
 * @description 工作区目录树形折叠浏览与原生文件操作组件。
 * 遵循极简原则，默认收起各层级文件夹，按需懒展开，支持多级目录层级缩进、
 * 按拼音及类型排序、全路径搜索过滤、系统资源管理器穿透定位、以及原生 OS 拖出与拖入复制。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  File,
  FileCode,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderOpen,
  RotateCw,
  Search,
  X
} from 'lucide-react'
import type { WorkspaceFileEntry } from '../../../shared/protocol'

/**
 * 工作区目录浏览组件属性接口。
 */
interface WorkspaceDirectoryProps {
  /** 当前工作区本地文件系统物理路径 */
  workspace: string
  /** 触发选择本地工作区目录回调 */
  onChooseWorkspace(): void
  /** 上次运行保存标识，发生变动时触发目录自动刷新 */
  lastRunFinished?: number | boolean | null
}

/**
 * 格式化文件字节大小为人类易读单位。
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * 根据文件名后缀获取对应文件类型图标。
 */
function getFileIcon(name: string, isDirectory: boolean) {
  if (isDirectory) return <Folder size={15} className="file-icon-dir" />
  const ext = name.split('.').at(-1)?.toLowerCase() ?? ''
  if (['py', 'js', 'ts', 'jsx', 'tsx', 'sh', 'sql', 'json', 'yaml', 'yml'].includes(ext)) {
    return <FileCode size={15} className="file-icon-code" />
  }
  if (['csv', 'tsv', 'xlsx', 'xls', 'parquet'].includes(ext)) {
    return <FileSpreadsheet size={15} className="file-icon-sheet" />
  }
  if (['txt', 'md', 'doc', 'docx', 'pdf'].includes(ext)) {
    return <FileText size={15} className="file-icon-doc" />
  }
  return <File size={15} className="file-icon-generic" />
}

/**
 * 判断指定文件或子目录在当前折叠状态下是否可见。
 * 规则：根目录项始终可见；子孙目录项必须在其所有上层父级目录都被展开时才可见。
 */
export function isItemVisible(item: WorkspaceFileEntry, expandedPaths: Set<string>): boolean {
  const parts = item.relativePath.split('/')
  // 根层级项目（无上级路径）默认直接显示
  if (parts.length <= 1) return true
  // 检查所有祖先路径是否都处于展开集合中
  let prefix = ''
  for (let i = 0; i < parts.length - 1; i++) {
    prefix = prefix ? `${prefix}/${parts[i]}` : parts[i]
    if (!expandedPaths.has(prefix)) {
      return false
    }
  }
  return true
}

/**
 * 工作区目录树形折叠浏览与文件操作组件。
 * 遵循极简原则，默认收起各层级文件夹，按需展开，避免目录全摊开造成的视觉混乱。
 */
export function WorkspaceDirectory({
  workspace,
  onChooseWorkspace,
  lastRunFinished
}: WorkspaceDirectoryProps) {
  const [files, setFiles] = useState<WorkspaceFileEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const dragDepth = useRef(0)
  const [statusMsg, setStatusMsg] = useState('')
  // 第一步：维护已展开文件夹的相对路径集合，默认全收起保持整洁
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => new Set())

  // 第二步：异步读取工作区目录条目列表
  async function loadDirectory(): Promise<void> {
    if (!workspace) {
      setFiles([])
      return
    }
    setLoading(true)
    try {
      const items = await window.medpeer.listWorkspaceDirectory(workspace)
      setFiles(items)
    } catch {
      setFiles([])
    } finally {
      setLoading(false)
    }
  }

  // 工作区或任务轮次变动时自动重新加载目录
  useEffect(() => {
    void loadDirectory()
  }, [workspace, lastRunFinished])

  // 第三步：收集所有文件夹相对路径，用于“全部收起 / 全部展开”切换
  const allFolderPaths = useMemo(() => {
    return files.filter(f => f.isDirectory).map(f => f.relativePath)
  }, [files])

  const allExpanded = allFolderPaths.length > 0 && allFolderPaths.every(p => expandedPaths.has(p))

  const toggleAll = () => {
    if (allExpanded) {
      setExpandedPaths(new Set())
    } else {
      setExpandedPaths(new Set(allFolderPaths))
    }
  }

  // 第四步：单项文件夹折叠/展开切换
  const toggleFolder = (folderRelPath: string) => {
    setExpandedPaths(prev => {
      const next = new Set(prev)
      if (next.has(folderRelPath)) {
        // 收起当前目录，并同步收起其下所有子孙目录
        for (const p of next) {
          if (p === folderRelPath || p.startsWith(`${folderRelPath}/`)) {
            next.delete(p)
          }
        }
      } else {
        next.add(folderRelPath)
      }
      return next
    })
  }

  // 第五步：根据搜索词过滤或根据折叠树状态展示条目
  const displayedFiles = useMemo(() => {
    const q = filter.trim().toLowerCase()
    // 搜索模式下展开展示所有匹配项，便于直接穿透查找
    if (q) {
      return files.filter(item =>
        item.name.toLowerCase().includes(q) || item.relativePath.toLowerCase().includes(q)
      )
    }
    // 正常模式下仅展示根层级以及上级已展开的条目
    return files.filter(item => isItemVisible(item, expandedPaths))
  }, [files, filter, expandedPaths])

  // 第六步：处理外部文件拖拽放入工作区
  const handleDrop = async (e: React.DragEvent) => {
    if (![...e.dataTransfer.types].includes('Files')) return
    e.preventDefault()
    e.stopPropagation()
    dragDepth.current = 0
    setDragOver(false)
    if (!workspace) return
    const droppedFiles = Array.from(e.dataTransfer.files)
    if (!droppedFiles.length) return
    try {
      const copied = await window.medpeer.copyDroppedFilesToWorkspace(droppedFiles, workspace)
      setStatusMsg(`已复制 ${copied.length} 个文件到目录`)
      setTimeout(() => setStatusMsg(''), 3000)
      await loadDirectory()
    } catch (err) {
      setStatusMsg(String(err))
      setTimeout(() => setStatusMsg(''), 4000)
    }
  }

  // 第七步：处理文件拖拽拖出（原生 OS 拖拽与文本传输）
  const handleDragStart = (e: React.DragEvent, item: WorkspaceFileEntry) => {
    e.dataTransfer.setData('text/plain', item.relativePath)
    void window.medpeer.dragWorkspaceFile(item.path)
  }

  // 第八步：处理条目点击打开或折叠展开
  const handleItemClick = (item: WorkspaceFileEntry) => {
    if (item.isDirectory) {
      toggleFolder(item.relativePath)
    } else {
      void window.medpeer.openWorkspaceFile(item.path)
    }
  }

  // 未选择工作区时的指引状态
  if (!workspace) {
    return (
      <section className="detail-section workspace-dir-empty-state">
        <div className="dir-empty-box">
          <FolderOpen size={36} className="dir-empty-icon" />
          <strong>未关联工作目录</strong>
          <p>关联本地工作区后，对话中生成的报告、数据与引用文件将直接在此查看、打开和拖拽。</p>
          <button className="primary-action" onClick={onChooseWorkspace}>
            <FolderOpen size={15} /> 选择本地工作区
          </button>
        </div>
      </section>
    )
  }

  const workspaceName = workspace.split(/[\\/]/).filter(Boolean).at(-1) ?? workspace

  return (
    <section
      className={`detail-section workspace-dir-panel ${dragOver ? 'is-dragover' : ''}`}
      onDragEnterCapture={e => {
        if (![...e.dataTransfer.types].includes('Files')) return
        e.preventDefault()
        dragDepth.current++
        setDragOver(true)
      }}
      onDragOver={e => {
        if (![...e.dataTransfer.types].includes('Files')) return
        e.preventDefault()
        e.stopPropagation()
        e.dataTransfer.dropEffect = 'copy'
      }}
      onDragLeaveCapture={e => {
        if (![...e.dataTransfer.types].includes('Files')) return
        dragDepth.current = Math.max(0, dragDepth.current - 1)
        if (!dragDepth.current) setDragOver(false)
      }}
      onDrop={handleDrop}
    >
      <div className="workspace-dir-header">
        <div className="dir-title-row">
          <div className="dir-title-info" title={workspace}>
            <Folder size={15} />
            <strong className="dir-name">{workspaceName}</strong>
          </div>
          <div className="dir-top-actions">
            {allFolderPaths.length > 0 && !filter && (
              <button
                className="icon-action"
                title={allExpanded ? '全部收起目录' : '全部展开目录'}
                aria-label={allExpanded ? '全部收起目录' : '全部展开目录'}
                onClick={toggleAll}
              >
                {allExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
            )}
            <button
              className="icon-action"
              title="在系统文件管理器中打开此目录"
              aria-label="打开所在目录"
              onClick={() => void window.medpeer.openWorkspaceFolder(workspace)}
            >
              <ExternalLink size={14} />
            </button>
            <button
              className="icon-action"
              title="刷新文件列表"
              aria-label="刷新目录"
              onClick={() => void loadDirectory()}
            >
              <RotateCw size={14} className={loading ? 'spinning' : ''} />
            </button>
          </div>
        </div>
        <div className="dir-path-footnote" title={workspace}>
          <span>{workspace}</span>
        </div>
      </div>

      <div className="dir-search-wrap">
        <Search size={13} className="dir-search-icon" />
        <input
          type="text"
          className="dir-search-input"
          placeholder="搜索当前目录文件…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
        {filter && (
          <button className="dir-search-clear" onClick={() => setFilter('')} aria-label="清空搜索">
            <X size={12} />
          </button>
        )}
      </div>

      {statusMsg && <div className="dir-status-pill">{statusMsg}</div>}

      <div className="dir-meta-bar">
        <span>
          {filter
            ? `匹配 ${displayedFiles.length} 项`
            : `${allFolderPaths.length} 目录 · ${files.length - allFolderPaths.length} 文件`}
        </span>
        <span className="dir-meta-hint">
          {filter ? '搜索模式' : '点击文件夹折叠/展开 · 文件可拖拉'}
        </span>
      </div>

      {dragOver && (
        <div className="dir-drag-hint">
          <FolderOpen size={24} />
          <strong>松开以复制到「{workspaceName}」</strong>
          <span>目标：工作区根目录</span>
          <small>{workspace}</small>
        </div>
      )}

      <div className="dir-file-list" role="list">
        {displayedFiles.length > 0 ? (
          displayedFiles.map(item => {
            const depth = filter ? 0 : item.relativePath.split('/').length - 1
            const isExpanded = expandedPaths.has(item.relativePath)

            return (
              <div
                key={item.path}
                className={`dir-file-row ${item.isDirectory ? 'is-dir' : 'is-file'}`}
                style={{ paddingLeft: `${6 + depth * 14}px` }}
                draggable={!item.isDirectory}
                onDragStart={e => handleDragStart(e, item)}
                onClick={() => handleItemClick(item)}
                title={`${item.relativePath}\n${
                  item.isDirectory
                    ? '点击折叠/展开目录；右侧按钮可在文件管理器打开'
                    : '点击用系统默认应用打开；按住可拖拉文件'
                }`}
              >
                {item.isDirectory ? (
                  <button
                    className="dir-chevron-btn"
                    aria-label={isExpanded ? '收起目录' : '展开目录'}
                    onClick={e => {
                      e.stopPropagation()
                      toggleFolder(item.relativePath)
                    }}
                  >
                    {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  </button>
                ) : (
                  <span className="dir-chevron-spacer" />
                )}

                <span className="dir-row-icon">
                  {item.isDirectory ? (
                    isExpanded ? (
                      <FolderOpen size={15} className="file-icon-dir" />
                    ) : (
                      <Folder size={15} className="file-icon-dir" />
                    )
                  ) : (
                    getFileIcon(item.name, false)
                  )}
                </span>

                <div className="dir-row-main">
                  <span className="dir-row-name">{item.name}</span>
                  {!item.isDirectory && (
                    <span className="dir-row-sub">
                      {formatFileSize(item.size)}
                      {filter && item.relativePath !== item.name && (
                        <small className="dir-row-rel"> · {item.relativePath}</small>
                      )}
                    </span>
                  )}
                </div>

                <div className="dir-row-actions">
                  <button
                    className="icon-action-sm"
                    title={item.isDirectory ? '在文件管理器中打开此文件夹' : '在文件资源管理器中定位'}
                    aria-label={item.isDirectory ? '在管理器中打开' : '在管理器中定位'}
                    onClick={e => {
                      e.stopPropagation()
                      if (item.isDirectory) {
                        void window.medpeer.openWorkspaceFolder(item.path)
                      } else {
                        void window.medpeer.showItemInFolder(item.path)
                      }
                    }}
                  >
                    <ExternalLink size={12} />
                  </button>
                </div>
              </div>
            )
          })
        ) : (
          <p className="detail-empty">
            {filter ? '未找到匹配的文件' : '当前工作区暂无可见文件。可将外部文件拖入此区域添加。'}
          </p>
        )}
      </div>
    </section>
  )
}
