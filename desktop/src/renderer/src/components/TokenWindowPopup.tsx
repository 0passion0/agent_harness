/**
 * @file TokenWindowPopup.tsx
 * @description 背景信息窗口 Token 占用分析气泡弹窗组件。
 * 负责将会话上下文深度拆解为：系统提示词 (system_prompt)、技能 (skills)、MCP 及工具 (mcp)、
 * 工作区记忆 (memory)、历史对话 (history) 五大维度，呈现环形进度环、分色占比柱状条
 * 以及详细比例明细，并支持跳转调整最大 Token 上限。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useRef, useState } from 'react'
import { Activity, BookOpen, Cpu, Database, History, Layers3 } from 'lucide-react'
import type { SessionTokenReport, TokenSectionUsage } from '../../../shared/protocol'

/**
 * 组件属性接口契约。
 */
type Props = {
  /** 后端计算生成的当前会话 Token 窗口报告 */
  report?: SessionTokenReport | null
  /** 点击配置上限链接时的回调函数 */
  onOpenSettings?: () => void
}

/**
 * 将原始 Token 数值格式化为紧凑易读的缩写（如 1.2M、52k）。
 *
 * @param tokens - Token 数量
 * @returns 格式化后的简短字符串
 */
function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1).replace('.0', '')}M`
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1).replace('.0', '')}k`
  return String(tokens)
}

/** 各核心区块对应的视觉图标映射表 */
const SECTION_ICONS: Record<string, typeof Cpu> = {
  system_prompt: Cpu,
  skills: Layers3,
  mcp: Activity,
  memory: BookOpen,
  history: History,
}

/** 各核心区块对应的图表色值 */
const SECTION_COLORS: Record<string, string> = {
  system_prompt: '#818cf8',
  skills: '#38bdf8',
  mcp: '#34d399',
  memory: '#fbbf24',
  history: '#f43f5e',
}

/**
 * 会话上下文 Token 占用分析气泡弹窗组件。
 *
 * 第一步：解析传入的报告数据，计算总占比、剩余占比及各部分数值。
 * 第二步：维护鼠标移入/移出防抖定时器，控制气泡显示与隐藏。
 * 第三步：监听外部点击与 Escape 键盘事件，实现快速收起。
 * 第四步：渲染 SVG 环形进度条触发器与详细多色构成列表。
 *
 * @param props - 组件属性
 */
export function TokenWindowPopup({ report, onOpenSettings }: Props) {
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const enterTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 监听点击外部或按下 Escape 键时自动收起弹窗
  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: MouseEvent | TouchEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  // 第一步：提取容量上限与使用量，计算使用百分比
  const maxTokens = report?.max_tokens
  const usedTokens = report?.used_tokens
  const usedRatio = report?.used_ratio

  // 极小占比格式化：极小值保留 1 位小数，避免 0.2% 四舍五入退化为 0%
  let usedPctText = '待加载'
  let usedPctNum = 0
  if (usedRatio !== undefined) {
    const rawPct = usedRatio * 100
    usedPctNum = Math.min(100, Math.max(0, rawPct))
    usedPctText = `${rawPct.toFixed(rawPct < 1 ? 1 : 0)}%`
  }
  const remPctText = report ? `${(report.remaining_ratio * 100).toFixed(0)}%` : '待加载'
  const usedStr = usedTokens === undefined ? '待加载' : formatTokens(usedTokens)
  const maxStr = maxTokens === undefined ? '待加载' : formatTokens(maxTokens)

  // 第二步：装配规范的五个核心上下文区块数据
  const canonicalOrder = ['system_prompt', 'skills', 'mcp', 'memory', 'history']
  const sectionsList: Array<TokenSectionUsage & { key: string; icon: typeof Cpu; color: string }> = report ? canonicalOrder.map(key => {
    const existing = report.sections[key]
    return {
      ...existing,
      key,
      icon: SECTION_ICONS[key] || Database,
      color: SECTION_COLORS[key] || '#94a3b8',
    }
  }) : []

  // 补充后端扩展返回的自定义 section
  if (report?.sections) {
    for (const [key, item] of Object.entries(report.sections)) {
      if (!canonicalOrder.includes(key)) {
        sectionsList.push({
          ...item,
          key,
          icon: Database,
          color: '#a1a1aa',
        })
      }
    }
  }

  // 第三步：防抖鼠标悬停处理
  const handleMouseEnter = () => {
    if (leaveTimer.current) {
      clearTimeout(leaveTimer.current)
      leaveTimer.current = null
    }
    enterTimer.current = setTimeout(() => setOpen(true), 120)
  }

  const handleMouseLeave = () => {
    if (enterTimer.current) {
      clearTimeout(enterTimer.current)
      enterTimer.current = null
    }
    leaveTimer.current = setTimeout(() => setOpen(false), 200)
  }

  return (
    <div
      className="token-window-wrapper"
      ref={wrapperRef}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* 环形进度条触发按钮 */}
      <button
        type="button"
        className={`token-window-trigger ${open ? 'active' : ''}`}
        aria-label="查看上下文 Token 估算"
        title="查看上下文 Token 估算"
        onClick={() => setOpen(prev => !prev)}
      >
        <span className="token-progress-ring">
          <svg viewBox="0 0 36 36" className="circular-chart">
            <path
              className="circle-bg"
              d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
            />
            <path
              className="circle"
              strokeDasharray={`${usedPctNum}, 100`}
              d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
            />
          </svg>
        </span>
        <span className="token-trigger-label">{usedPctText === '待加载' ? '估算待加载' : `${usedPctText} 已用`}</span>
      </button>

      {/* 悬停/点击浮现的详细分析气泡 */}
      {open && (
        <div className="token-window-bubble" role="tooltip">
          <div className="token-bubble-header">
            <div className="token-bubble-title">背景信息窗口:</div>
            <div className="token-bubble-headline">
              <strong>{usedPctText === '待加载' ? '上下文估算待加载' : `${usedPctText} 已用`}</strong>
              <span className="token-bubble-sub">（剩余 {remPctText}）</span>
            </div>
            <div className="token-bubble-counts">
              已用 {usedStr} 标记，共 {maxStr}
            </div>
          </div>

          {/* 分色横向构成柱状图 */}
          <div className="token-bar-track">
            {sectionsList.map(s => {
              if (s.tokens === null || s.ratio_of_used === null || s.tokens <= 0) return null
              const widthPct = Math.max(2.5, (s.ratio_of_max || 0) * 100)
              return (
                <div
                  key={s.key}
                  className="token-bar-fill"
                  style={{ width: `${widthPct}%`, backgroundColor: s.color }}
                  title={`${s.name}: ${formatTokens(s.tokens)} Token (${(s.ratio_of_used * 100).toFixed(1)}%)`}
                />
              )
            })}
          </div>

          {/* 各细项明细列表 */}
          <div className="token-sections-breakdown">
            <div className="breakdown-header">Token 构成占比（估算）</div>
            {sectionsList.map(item => {
              const Icon = item.icon
              const usedRatioPct = item.ratio_of_used === null ? null : (item.ratio_of_used * 100).toFixed(1)
              return (
                <div className="breakdown-row" key={item.key}>
                  <div className="breakdown-left">
                    <span className="breakdown-dot" style={{ backgroundColor: item.color }} />
                    <Icon size={13} className="breakdown-icon" />
                    <span className="breakdown-name">{item.name}</span>
                  </div>
                  <div className="breakdown-right">
                    <span className="breakdown-tokens">{item.tokens === null ? '未统计' : formatTokens(item.tokens)}</span>
                    {usedRatioPct !== null && <span className="breakdown-ratio">({usedRatioPct}%)</span>}
                  </div>
                </div>
              )
            })}
          </div>

          <div className="token-bubble-counts">
            {report?.summary_text || '尚未采集实际模型请求；各项占比为估算。'}
          </div>

          {onOpenSettings && (
            <div className="token-bubble-footer">
              <button
                type="button"
                className="token-settings-link"
                onClick={() => {
                  setOpen(false)
                  onOpenSettings()
                }}
              >
                配置最大 Token 限制
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
