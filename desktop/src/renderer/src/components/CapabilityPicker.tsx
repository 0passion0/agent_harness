/**
 * @file CapabilityPicker.tsx
 * @description 参考 OpenAI Codex / ChatGPT macOS 原生交互风格的现代化命令与能力选择浮层。
 * 支持在输入框中输入斜杠 '/' 唤起或点击 '+' 按钮展开，提供快捷指令、插件、专业技能的分组浏览、
 * 实时模糊搜索过滤、键盘上下键导航与回车即选能力。
 * @author tongliga
 * @date 2026-10-19
 */

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import {
  Activity, Box, Check, Plus, Puzzle, RotateCcw, Search, Wrench, X
} from 'lucide-react'
import type { ApplicationState, AppPlugin, SkillDescriptor } from '../../../shared/protocol'

/** 快捷指令项契约 */
export type QuickCommand = {
  id: string
  category: 'command'
  name: string
  description: string
  icon: 'rotate-ccw' | 'activity' | 'wrench'
  prompt?: string
}

/** 统一浮层条目契约 */
export type PaletteItem =
  | QuickCommand
  | {
      id: string
      category: 'plugin'
      name: string
      description: string
      version: string
      selected: boolean
      plugin: AppPlugin
    }
  | {
      id: string
      category: 'skill'
      name: string
      description: string
      source: string
      selected: boolean
      skill: SkillDescriptor
    }

export type CapabilityPickerProps = {
  application: ApplicationState | null
  plugins: string[]
  skills: string[]
  setPlugins(ids: string[]): void
  setSkills(ids: string[]): void
  disabled?: boolean
  onEscape?(): void
  /** 外部输入搜索过滤词（例如在输入框中键入 / 后跟随的文本） */
  externalQuery?: string
  /** 点击或回车应用预设提示词回调 */
  onSelectPrompt?(prompt: string): void
  /** 应用能力后清理斜杠等状态的回调 */
  onCapabilityApplied?(): void
  /** 重置并清空当前会话回调 */
  onClearSession?(): void
  /** 切换执行过程抽屉显示状态回调 */
  onToggleDetail?(): void
  /** 打开系统偏好设置面板回调 */
  onOpenSettings?(tab?: string): void
}

/** 外部命令控制器句柄 */
export type CapabilityPickerHandle = {
  open(query?: string): void
  close(): void
  isOpen(): boolean
  handleKeyDown?(event: React.KeyboardEvent): boolean
}

/**
 * 判断是否触发能力选择快捷键（单按无修饰的斜杠 '/'）。
 *
 * @param event 键盘事件
 * @returns 是否匹配快捷触发规则
 */
export function isCapabilityShortcut(event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey'>): boolean {
  return event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey
}

/** 内置核心快捷指令清单 */
const builtinCommands: QuickCommand[] = [
  {
    id: 'cmd-reset',
    category: 'command',
    name: '新建会话',
    description: '重置并清空当前任务上下文，开启全新任务会话',
    icon: 'rotate-ccw'
  },
  {
    id: 'cmd-activity',
    category: 'command',
    name: '任务详情',
    description: '展开右侧面板，查看智能体运行过程与工具执行记录',
    icon: 'activity'
  },
  {
    id: 'cmd-settings',
    category: 'command',
    name: '插件与设置',
    description: '打开偏好设置，配置扩展插件、技能与统一模型',
    icon: 'wrench'
  }
]

/**
 * 渲染指令对应图标。
 */
function renderCommandIcon(icon: QuickCommand['icon']) {
  switch (icon) {
    case 'rotate-ccw': return <RotateCcw size={15} />
    case 'activity': return <Activity size={15} />
    case 'wrench': return <Wrench size={15} />
  }
}

/**
 * Codex 风格命令与能力选择器组件。
 */
export const CapabilityPicker = forwardRef<CapabilityPickerHandle, CapabilityPickerProps>(
  function CapabilityPicker(
    {
      application,
      plugins,
      skills,
      setPlugins,
      setSkills,
      disabled = false,
      onEscape,
      externalQuery = '',
      onSelectPrompt,
      onCapabilityApplied,
      onClearSession,
      onToggleDetail,
      onOpenSettings
    },
    ref
  ) {
    // 第一步：状态定义与 DOM 节点引用
    const [open, setOpen] = useState(false)
    const [internalQuery, setInternalQuery] = useState('')
    const [activeIndex, setActiveIndex] = useState(0)
    const pickerRef = useRef<HTMLDivElement>(null)
    const paletteRef = useRef<HTMLDivElement>(null)
    const searchInputRef = useRef<HTMLInputElement>(null)
    const listRef = useRef<HTMLDivElement>(null)

    // 第二步：合并外部查询（输入框 /xxx）与内部搜索框查询
    const query = (externalQuery || internalQuery).trim().toLowerCase()

    // 禁用时自动关闭浮层
    useEffect(() => {
      if (disabled && open) setOpen(false)
    }, [disabled, open])

    // 第三步：抽取当前可用插件与技能列表
    const availablePlugins = useMemo(() => {
      return application?.plugins.filter(item => item.enabled) ?? []
    }, [application?.plugins])

    const availableSkills = useMemo(() => {
      return (
        application?.skills.filter(
          item => item.enabled && (!item.plugin_id || availablePlugins.some(p => p.id === item.plugin_id))
        ) ?? []
      )
    }, [application?.skills, availablePlugins])

    const selectedPlugins = useMemo(() => {
      return availablePlugins.filter(item => plugins.includes(item.id))
    }, [availablePlugins, plugins])

    const selectedSkills = useMemo(() => {
      return availableSkills.filter(item => skills.includes(item.id))
    }, [availableSkills, skills])

    // 第四步：根据搜索词过滤出三大分组条目
    const { filteredCommands, filteredPlugins, filteredSkills, flattenedItems } = useMemo(() => {
      const match = (text: string) => !query || text.toLowerCase().includes(query)

      const commands: QuickCommand[] = builtinCommands.filter(
        cmd => match(cmd.name) || match(cmd.description) || match(cmd.id)
      )

      const pluginItems: PaletteItem[] = availablePlugins
        .filter(p => match(p.name) || match(p.description) || match(p.id))
        .map(p => ({
          id: p.id,
          category: 'plugin',
          name: p.name,
          description: p.description,
          version: p.version,
          selected: plugins.includes(p.id),
          plugin: p
        }))

      const skillItems: PaletteItem[] = availableSkills
        .filter(s => !s.plugin_id && (match(s.name) || match(s.description) || match(s.id)))
        .map(s => ({
          id: s.id,
          category: 'skill',
          name: s.name,
          description: s.description || '执行特定专业分析与操作的技能工作流',
          source: s.source || '个人',
          selected: skills.includes(s.id),
          skill: s
        }))

      const flattened: PaletteItem[] = [...commands, ...pluginItems, ...skillItems]
      return {
        filteredCommands: commands,
        filteredPlugins: pluginItems,
        filteredSkills: skillItems,
        flattenedItems: flattened
      }
    }, [query, availablePlugins, availableSkills, plugins, skills])

    // 查询词变化时重置选中的项索引
    useEffect(() => {
      setActiveIndex(0)
    }, [query])

    // 选中项滚动到可视区域
    useEffect(() => {
      if (!open || !listRef.current) return
      const activeEl = listRef.current.querySelector<HTMLElement>(`[data-palette-index="${activeIndex}"]`)
      if (activeEl) {
        activeEl.scrollIntoView({ block: 'nearest' })
      }
    }, [activeIndex, open])

    // 第五步：处理条目点击与执行逻辑
    const executeItem = (item: PaletteItem) => {
      if (item.category === 'command') {
        if (item.prompt && onSelectPrompt) {
          onSelectPrompt(item.prompt)
        } else if (item.id === 'cmd-reset') {
          onClearSession?.()
        } else if (item.id === 'cmd-activity') {
          onToggleDetail?.()
        } else if (item.id === 'cmd-settings') {
          onOpenSettings?.('plugins')
        }
        setOpen(false)
        setInternalQuery('')
        onCapabilityApplied?.()
        return
      }

      if (item.category === 'plugin') {
        const next = plugins.includes(item.id)
          ? plugins.filter(id => id !== item.id)
          : [...plugins, item.id]
        setPlugins(next)
        onCapabilityApplied?.()
        setOpen(false)
        setInternalQuery('')
        return
      }

      if (item.category === 'skill') {
        const next = skills.includes(item.id)
          ? skills.filter(id => id !== item.id)
          : [...skills, item.id]
        setSkills(next)
        onCapabilityApplied?.()
        setOpen(false)
        setInternalQuery('')
        return
      }
    }

    // 键盘上下导航与回车响应
    const handleKeyDown = (event: React.KeyboardEvent): boolean => {
      if (!open) return false

      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActiveIndex(prev => (flattenedItems.length ? (prev + 1) % flattenedItems.length : 0))
        return true
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActiveIndex(prev =>
          flattenedItems.length ? (prev - 1 + flattenedItems.length) % flattenedItems.length : 0
        )
        return true
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        const target = flattenedItems[activeIndex]
        if (target) {
          executeItem(target)
        }
        return true
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setOpen(false)
        setInternalQuery('')
        onEscape?.()
        return true
      }
      return false
    }

    // 暴露 Imperative 句柄给宿主组件
    useImperativeHandle(ref, () => ({
      open: (initialQuery?: string) => {
        setOpen(true)
        if (initialQuery !== undefined) setInternalQuery(initialQuery)
        requestAnimationFrame(() => searchInputRef.current?.focus())
      },
      close: () => {
        setOpen(false)
        setInternalQuery('')
      },
      isOpen: () => open,
      handleKeyDown
    }))

    // 点击浮层外部时自动关闭
    useEffect(() => {
      if (!open) return
      const handlePointerDown = (event: MouseEvent) => {
        const target = event.target as Node
        if (pickerRef.current && !pickerRef.current.contains(target) &&
            paletteRef.current && !paletteRef.current.contains(target)) {
          setOpen(false)
          setInternalQuery('')
          onEscape?.()
        }
      }
      document.addEventListener('mousedown', handlePointerDown)
      return () => document.removeEventListener('mousedown', handlePointerDown)
    }, [open, onEscape])

    let itemCounter = 0

    return (
      <div className="capability-picker" ref={pickerRef}>
        {/* 底部输入框工具条触发按钮 */}
        <button
          type="button"
          className={`attach-control ${open ? 'active' : ''}`}
          disabled={disabled}
          aria-label="选择扩展能力与快捷指令 (/)"
          title="选择插件、技能或快捷指令 (输入 / 快速唤起)"
          onClick={() => {
            const next = !open
            setOpen(next)
            if (next) {
              requestAnimationFrame(() => searchInputRef.current?.focus())
            } else {
              setInternalQuery('')
            }
          }}
        >
          <Plus size={16} />
        </button>

        {/* 已选插件胶囊标签 */}
        {selectedPlugins.map(item => (
          <div className="capability-chip plugin" key={item.id} title={item.description}>
            <Puzzle size={12} className="chip-icon" />
            <span className="chip-name">{item.name}</span>
            <button
              type="button"
              className="chip-remove"
              aria-label={`移除插件 ${item.name}`}
              onClick={e => {
                e.stopPropagation()
                setPlugins(plugins.filter(id => id !== item.id))
              }}
            >
              <X size={11} />
            </button>
          </div>
        ))}

        {/* 已选技能胶囊标签 */}
        {selectedSkills.map(item => (
          <div className="capability-chip skill" key={item.id} title={item.description}>
            <Box size={12} className="chip-icon" />
            <span className="chip-name">{item.name}</span>
            <button
              type="button"
              className="chip-remove"
              aria-label={`移除技能 ${item.name}`}
              onClick={e => {
                e.stopPropagation()
                setSkills(skills.filter(id => id !== item.id))
              }}
            >
              <X size={11} />
            </button>
          </div>
        ))}

        {/* 现代 Codex 风格悬浮能力与指令调色板 */}
        {open && (
          <div
            className="capability-palette"
            ref={paletteRef}
            role="menu"
            aria-label="命令与能力选择"
            onKeyDown={handleKeyDown}
          >
            {/* 顶栏实时搜索框（非外部 / 模式时聚焦使用） */}
            <div className="capability-palette-search">
              <Search size={14} className="search-icon" />
              <input
                ref={searchInputRef}
                type="text"
                placeholder="搜索指令、插件与技能…"
                aria-label="搜索指令、插件与技能"
                value={internalQuery || externalQuery}
                onChange={e => setInternalQuery(e.target.value)}
                onKeyDown={handleKeyDown}
              />
              {(internalQuery || externalQuery) && (
                <button
                  type="button"
                  className="search-clear"
                  aria-label="清空搜索"
                  onClick={() => setInternalQuery('')}
                >
                  <X size={13} />
                </button>
              )}
            </div>

            {/* 滚动列表区域 */}
            <div className="capability-palette-list" ref={listRef} role="listbox">
              {flattenedItems.length === 0 ? (
                <div className="capability-empty">
                  未找到与“{query}”相匹配的指令或扩展能力
                </div>
              ) : (
                <>
                  {/* 分组一：快捷指令 */}
                  {filteredCommands.length > 0 && (
                    <div className="capability-group">
                      <div className="capability-section-title">快捷指令</div>
                      {filteredCommands.map(cmd => {
                        const index = itemCounter++
                        const isActive = index === activeIndex
                        return (
                          <button
                            type="button"
                            key={cmd.id}
                            data-palette-index={index}
                            className={`capability-item ${isActive ? 'active' : ''}`}
                            role="option"
                            aria-selected={isActive}
                            title={cmd.description}
                            onMouseEnter={() => setActiveIndex(index)}
                            onClick={() => executeItem(cmd)}
                          >
                            <span className="capability-item-icon">
                              {renderCommandIcon(cmd.icon)}
                            </span>
                            <span className="capability-item-main">
                              <span className="capability-item-name">{cmd.name}</span>
                              <span className="capability-item-desc">{cmd.description}</span>
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  )}

                  {/* 分组二：插件 */}
                  {filteredPlugins.length > 0 && (
                    <div className="capability-group">
                      <div className="capability-section-title">插件</div>
                      {filteredPlugins.map(item => {
                        const index = itemCounter++
                        const isActive = index === activeIndex
                        const isSelected = item.category === 'plugin' && item.selected
                        return (
                          <button
                            type="button"
                            key={item.id}
                            data-palette-index={index}
                            className={`capability-item ${isActive ? 'active' : ''} ${isSelected ? 'selected' : ''}`}
                            role="option"
                            aria-selected={isActive}
                            title={item.description}
                            onMouseEnter={() => setActiveIndex(index)}
                            onClick={() => executeItem(item)}
                          >
                            <span className="capability-item-icon plugin">
                              <Puzzle size={15} />
                            </span>
                            <span className="capability-item-main">
                              <span className="capability-item-name">{item.name}</span>
                              <span className="capability-item-desc">{item.description}</span>
                            </span>
                            {isSelected && <Check size={14} className="capability-item-check" aria-label="已添加" />}
                          </button>
                        )
                      })}
                    </div>
                  )}

                  {/* 分组三：技能 */}
                  {filteredSkills.length > 0 && (
                    <div className="capability-group">
                      <div className="capability-section-title">技能</div>
                      {filteredSkills.map(item => {
                        const index = itemCounter++
                        const isActive = index === activeIndex
                        const isSelected = item.category === 'skill' && item.selected
                        return (
                          <button
                            type="button"
                            key={item.id}
                            data-palette-index={index}
                            className={`capability-item ${isActive ? 'active' : ''} ${isSelected ? 'selected' : ''}`}
                            role="option"
                            aria-selected={isActive}
                            title={item.description}
                            onMouseEnter={() => setActiveIndex(index)}
                            onClick={() => executeItem(item)}
                          >
                            <span className="capability-item-icon skill">
                              <Box size={15} />
                            </span>
                            <span className="capability-item-main">
                              <span className="capability-item-name">{item.name}</span>
                              <span className="capability-item-desc">{item.description}</span>
                            </span>
                            {isSelected && <Check size={14} className="capability-item-check" aria-label="已添加" />}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </>
              )}
            </div>

            {/* 调色板底部状态与快捷链接 */}
            <div className="capability-palette-footer">
              <span className="footer-shortcuts">↑↓ 选择　↵ 确认　Esc 关闭</span>
              <button
                type="button"
                className="footer-manage"
                onClick={() => {
                  setOpen(false)
                  onOpenSettings?.('plugins')
                }}
              >
                管理插件
              </button>
            </div>
          </div>
        )}
      </div>
    )
  }
)
