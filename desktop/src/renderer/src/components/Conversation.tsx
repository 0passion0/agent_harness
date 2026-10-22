/**
 * @file Conversation.tsx
 * @description 智能体对话消息流与交付物呈现组件。
 * 负责渲染会话历史消息（包含 Human 提示词与 AI 响应）、高颜值实时流式文本生成效果、
 * Markdown 格式化排版、代码块独立复制与语言标识、文本内文件与目录一键点击穿透、
 * 后台 LLM 思考与工具调用实时展示、成果图片预览、交付物交互卡片以及智能近底滚动控制。
 * @author tongliga
 * @date 2026-10-19
 */

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import mermaid from 'mermaid'
import Prism from 'prismjs'
import 'prismjs/components/prism-javascript'
import 'prismjs/components/prism-typescript'
import 'prismjs/components/prism-jsx'
import 'prismjs/components/prism-tsx'
import 'prismjs/components/prism-python'
import 'prismjs/components/prism-bash'
import 'prismjs/components/prism-json'
import 'prismjs/components/prism-markdown'
import 'prismjs/components/prism-sql'
import 'prismjs/components/prism-yaml'
import 'prismjs/components/prism-css'
import { ArrowDown, BarChart3, Check, Code, Copy, Download, Eye, Maximize2, Minimize2, Minus, Plus, RotateCcw } from 'lucide-react'
import type { AgentState } from '../state/agent-state'
import type { ArtifactRef, Snapshot, RunRecord } from '../../../shared/protocol'
import { cleanPath, isFilePath } from './FileLink'
import { InlineActivity } from './InlineActivity'
import './conversation.css'

/** 支持在对话流内直接预览的图片 MIME 格式列表 */
const previewImageTypes = ['image/png', 'image/jpeg', 'image/webp']

/**
 * 安全提取消息载荷中的文本内容。
 * 兼容纯字符串、LangChain 多段 content 数组或未识别的 JSON 结构。
 *
 * @param content - 原始消息内容载荷
 * @returns 提取合并后的纯文本字符串
 */
export function contentText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map(block => typeof block === 'object' && block !== null && 'text' in block && typeof block.text === 'string' ? block.text : '')
      .join('\n')
  }
  return JSON.stringify(content) ?? ''
}

/**
 * 消息单项快速复制按钮组件。
 */
function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    void navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  return (
    <button
      type="button"
      className={`msg-copy-btn ${copied ? 'is-copied' : ''}`}
      title={copied ? '已复制到剪贴板' : label}
      aria-label={copied ? '已复制' : label}
      onClick={handleCopy}
    >
      {copied ? <Check size={12} className="copy-icon-check" /> : <Copy size={12} />}
      <span className="copy-label">{copied ? '已复制' : label}</span>
    </button>
  )
}

/**
 * 成果图片懒加载组件。
 * 支持 `medpeer-artifact:` 虚拟协议地址，并通过主进程安全读取 base64 数据进行就地渲染。
 */
function ArtifactImage({ src, alt }: { src?: string; alt?: string }) {
  const [data, setData] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setError('')
    setData('')
    // 非 medpeer-artifact 协议按标准 data:image 链接渲染
    if (!src?.startsWith('medpeer-artifact:')) {
      setData(src && /^data:image\/(png|jpeg|webp);/i.test(src) ? src : '')
      return
    }
    let current = true
    const relativePath = decodeURIComponent(src.slice('medpeer-artifact:'.length))
    window.medpeer.readArtifact(relativePath).then(result => {
      if (current && previewImageTypes.includes(result.media_type)) {
        setData(`data:${result.media_type};base64,${result.data}`)
      }
    }).catch(reason => {
      if (current) {
        setData('')
        setError(String(reason))
      }
    })
    return () => { current = false }
  }, [src])

  return data ? (
    <img src={data} alt={alt ?? ''} loading="lazy" />
  ) : (
    <span className="muted" role={error ? 'alert' : undefined}>{error || '图片不可预览'}</span>
  )
}

const mermaidCache = new Map<string, { svg: string; error?: string }>()

let lastMermaidTheme = ''
function ensureMermaidTheme(isDark: boolean) {
  if (typeof window === 'undefined') return
  const theme = isDark ? 'dark' : 'neutral'
  if (lastMermaidTheme === theme) return
  lastMermaidTheme = theme
  try {
    mermaid.initialize({
      startOnLoad: false,
      suppressErrorRendering: true,
      theme,
      securityLevel: 'loose',
      htmlLabels: false,
      fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    })
  } catch {
    // 忽略重复初始化异常
  }
}

function svgPreviewDocument(svg: string) {
  const tag = svg.match(/<svg\b[^>]*>/i)?.[0]
  if (!tag) return ''
  const responsiveTag = tag.replace(/\s(?:width|height)=(?:"[^"]*"|'[^']*')/gi, '').replace(/>$/, ' width="100%" height="100%">')
  const responsiveSvg = svg.replace(tag, responsiveTag)
  return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'"><style>html,body{width:100%;height:100%;margin:0;overflow:hidden;background:transparent}body{display:grid;place-items:center}svg{width:100%!important;height:100%!important;max-width:none!important}</style></head><body>${responsiveSvg}</body></html>`
}

function SvgDiagram({ svg, filename = 'diagram.png', allowSvgDownload = false }: { svg: string; filename?: string; allowSvgDownload?: boolean }) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [error, setError] = useState('')
  const [fullscreen, setFullscreen] = useState(false)
  const viewer = useRef<HTMLDivElement>(null)
  const container = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null)
  const changeZoom = (delta: number) => setZoom(value => Math.min(3, Math.max(0.4, value + delta)))

  useEffect(() => {
    const handleFullscreenChange = () => {
      const active = document.fullscreenElement === viewer.current
      setFullscreen(active)
      setZoom(1)
      setPan({ x: 0, y: 0 })
    }
    document.addEventListener('fullscreenchange', handleFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }, [])

  useEffect(() => {
    if (!fullscreen) return
    const handleWheel = (event: WheelEvent) => {
      if (!container.current?.contains(event.target as Node)) return
      event.preventDefault()
      if (event.deltaY) changeZoom(event.deltaY < 0 ? 0.1 : -0.1)
    }
    window.addEventListener('wheel', handleWheel, { capture: true, passive: false })
    return () => window.removeEventListener('wheel', handleWheel, true)
  }, [fullscreen])

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === viewer.current) await document.exitFullscreen()
      else await viewer.current?.requestFullscreen()
      setFullscreen(document.fullscreenElement === viewer.current)
      setError('')
    } catch {
      setError('无法切换全屏模式')
    }
  }

  const handleDownloadSvg = () => {
    const content = /<svg\b[^>]*\bxmlns=/i.test(svg) ? svg : svg.replace(/<svg\b/i, '<svg xmlns="http://www.w3.org/2000/svg"')
    const url = URL.createObjectURL(new Blob([content], { type: 'image/svg+xml;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = filename.replace(/\.png$/i, '.svg')
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const handleDownload = async () => {
    let svgUrl = ''
    let pngUrl = ''
    try {
      const source = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement
      if (!(source instanceof SVGSVGElement)) throw new Error('找不到 SVG 图表')
      const viewBox = source.viewBox.baseVal
      const width = viewBox.width || source.width.baseVal.value
      const height = viewBox.height || source.height.baseVal.value
      if (!width || !height) throw new Error('SVG 图表没有有效尺寸')
      source.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
      source.setAttribute('width', String(width))
      source.setAttribute('height', String(height))
      svgUrl = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(source)], { type: 'image/svg+xml;charset=utf-8' }))
      const image = new Image()
      await new Promise<void>((resolve, reject) => {
        image.onload = () => resolve()
        image.onerror = () => reject(new Error('无法读取 SVG 图表'))
        image.src = svgUrl
      })
      const canvas = document.createElement('canvas')
      canvas.width = Math.ceil(width * 2)
      canvas.height = Math.ceil(height * 2)
      const context = canvas.getContext('2d')
      if (!context) throw new Error('无法创建图片画布')
      context.scale(2, 2)
      context.fillStyle = getComputedStyle(container.current!).backgroundColor || '#fff'
      context.fillRect(0, 0, width, height)
      context.drawImage(image, 0, 0, width, height)
      const png = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('PNG 编码失败')), 'image/png'))
      pngUrl = URL.createObjectURL(png)
      const link = document.createElement('a')
      link.download = filename
      link.href = pngUrl
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(pngUrl), 1000)
      setError('')
    } catch {
      setError('PNG 导出失败，请重试')
      if (pngUrl) URL.revokeObjectURL(pngUrl)
    } finally {
      if (svgUrl) URL.revokeObjectURL(svgUrl)
    }
  }

  return (
    <div ref={viewer} className="svg-diagram-shell">
      <div className="mermaid-zoom-actions" aria-label="图表缩放">
        <button type="button" className="mermaid-action-btn" onClick={() => void toggleFullscreen()} title={fullscreen ? '退出全屏' : '全屏查看图表'} aria-label={fullscreen ? '退出全屏' : '全屏查看图表'}>{fullscreen ? <Minimize2 size={12} /> : <Maximize2 size={12} />}<span>{fullscreen ? '退出全屏' : '全屏'}</span></button>
        <button type="button" className="mermaid-action-btn" disabled={!fullscreen} onClick={() => changeZoom(-0.2)} title="缩小" aria-label="缩小图表"><Minus size={12} /></button>
        <span>{Math.round(zoom * 100)}%</span>
        <button type="button" className="mermaid-action-btn" disabled={!fullscreen} onClick={() => changeZoom(0.2)} title="放大" aria-label="放大图表"><Plus size={12} /></button>
        <button type="button" className="mermaid-action-btn" disabled={!fullscreen} onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }} title="重置视图" aria-label="重置图表视图"><RotateCcw size={12} /></button>
        <button type="button" className="mermaid-action-btn" onClick={() => void handleDownload()} title="下载 PNG" aria-label="下载图表为 PNG"><Download size={12} /><span>PNG</span></button>
        {allowSvgDownload && <button type="button" className="mermaid-action-btn" onClick={handleDownloadSvg} title="下载 SVG" aria-label="下载 SVG"><Download size={12} /><span>SVG</span></button>}
      </div>
      <div
        ref={container}
        className="mermaid-svg-container"
        onPointerDown={event => {
          if (!fullscreen || event.button !== 0) return
          drag.current = { x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y }
          event.currentTarget.setPointerCapture(event.pointerId)
        }}
        onPointerMove={event => {
          if (drag.current) setPan({ x: drag.current.panX + event.clientX - drag.current.x, y: drag.current.panY + event.clientY - drag.current.y })
        }}
        onPointerUp={() => { drag.current = null }}
        onPointerCancel={() => { drag.current = null }}
      >
        <div className="mermaid-svg-content" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
          <iframe title="SVG 图表预览" srcDoc={svgPreviewDocument(svg)} sandbox="" className="svg-preview-frame" />
        </div>
        {fullscreen && <span className="mermaid-pan-hint">滚轮缩放 · 拖动平移</span>}
        {error && <span className="mermaid-export-error" role="alert">{error}</span>}
      </div>
    </div>
  )
}

/**
 * 高级 Mermaid 矢量图表渲染组件。
 * 智能将 ```mermaid 代码块即时渲染为 SVG 矢量图表，
 * 并支持图表视图与源代码视图一键无缝切换、复制源码和异常友好提示。
 * 内置全局 SVG 缓存与流式语法安全解析，杜绝滚动刷新与流式卡顿。
 */
function MermaidBlock({ code, isStreaming = false }: { code: string; isStreaming?: boolean }) {
  const isDark = typeof document !== 'undefined' && (document.querySelector('.product-v3.dark') !== null || document.body.classList.contains('dark'))
  const trimmed = code.trim()
  const cacheKey = `${isDark ? 'd' : 'l'}:${trimmed}`

  const cached = mermaidCache.get(cacheKey)
  const [svg, setSvg] = useState<string>(() => cached?.svg || '')
  const [error, setError] = useState<string | null>(() => cached?.error || null)
  const [viewMode, setViewMode] = useState<'diagram' | 'code'>('diagram')
  const [copied, setCopied] = useState(false)

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    void navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  useEffect(() => {
    // 若已有当前主题与源码的有效 SVG 缓存，直接同步复用，不重复发起 render
    if (cached?.svg) {
      setSvg(cached.svg)
      setError(null)
      return
    }

    if (typeof window === 'undefined') return

    let cancelled = false
    const id = `mermaid-${Math.random().toString(36).slice(2, 9)}`

    // 流式输入时使用 350ms 防抖，非流式稳定代码使用 50ms
    const timer = setTimeout(async () => {
      if (cancelled) return

      // 先做纯语法解析校验，语法未闭合前绝不执行 DOM 渲染
      try {
        ensureMermaidTheme(isDark)
        const parseResult = await mermaid.parse(trimmed, { suppressErrors: true })
        if (parseResult === false && isStreaming) {
          return
        }
      } catch (parseErr) {
        if (isStreaming) {
          // 流式生成阶段代码尚未闭合，属于正常现象，静默等待完成
          return
        }
        if (!cancelled) {
          const msg = parseErr instanceof Error ? parseErr.message : String(parseErr)
          setError(msg || '图表语法解析失败')
          mermaidCache.set(cacheKey, { svg: '', error: msg })
        }
        return
      }

      // 语法校验通过，执行矢量图 SVG 生成
      try {
        const result = await mermaid.render(id, trimmed)
        if (!cancelled) {
          setSvg(result.svg)
          setError(null)
          mermaidCache.set(cacheKey, { svg: result.svg })
        }
      } catch (renderErr) {
        if (!cancelled && !isStreaming) {
          const msg = renderErr instanceof Error ? renderErr.message : String(renderErr)
          setError(msg || '图表生成失败')
          mermaidCache.set(cacheKey, { svg: '', error: msg })
        }
      } finally {
        const tempNode = document.getElementById(`d${id}`)
        if (tempNode) tempNode.remove()
      }
    }, isStreaming ? 350 : 50)

    return () => {
      cancelled = true
      clearTimeout(timer)
      const tempNode = document.getElementById(`d${id}`)
      if (tempNode) tempNode.remove()
    }
  }, [trimmed, isStreaming, isDark, cacheKey, cached])

  return (
    <div className="modern-mermaid-block">
      <div className="modern-mermaid-header">
        <div className="modern-code-meta">
          <span className="code-dot red" />
          <span className="code-dot yellow" />
          <span className="code-dot green" />
          <span className="mermaid-badge">MERMAID 图表</span>
        </div>
        <div className="mermaid-actions">
          <div className="preview-toggle-group">
            <button
              type="button"
              className={`preview-toggle-btn ${viewMode === 'diagram' ? 'is-active' : ''}`}
              onClick={() => setViewMode('diagram')}
              title="切换为可视化图表"
            >
              <BarChart3 size={11} style={{ marginRight: 3, verticalAlign: 'middle' }} />
              图表
            </button>
            <button
              type="button"
              className={`preview-toggle-btn ${viewMode === 'code' ? 'is-active' : ''}`}
              onClick={() => setViewMode('code')}
              title="查看图表源代码"
            >
              <Code size={11} style={{ marginRight: 3, verticalAlign: 'middle' }} />
              源码
            </button>
          </div>
          <button
            type="button"
            className={`modern-code-copy ${copied ? 'is-copied' : ''}`}
            onClick={handleCopy}
            title={copied ? '已复制代码' : '复制代码'}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            <span>{copied ? '已复制' : '复制'}</span>
          </button>
        </div>
      </div>
      {viewMode === 'diagram' ? (
        error ? (
          <div className="mermaid-error">
            <div style={{ fontWeight: 600, marginBottom: 4 }}>图表渲染解析失败</div>
            <div style={{ fontSize: 11, opacity: 0.85, marginBottom: 8, whiteSpace: 'pre-wrap' }}>{error}</div>
            <button
              type="button"
              className="modern-code-copy"
              onClick={() => setViewMode('code')}
            >
              切换至源码视图
            </button>
          </div>
        ) : svg ? (
          <SvgDiagram svg={svg} filename="mermaid-diagram.png" />
        ) : (
          <div style={{ padding: '24px', textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
            <span className="streaming-pulse-dot" style={{ marginRight: 6 }} />
            {isStreaming ? '正在生成 Mermaid 图表...' : '正在解析并渲染 Mermaid 矢量图...'}
          </div>
        )
      ) : (
        <pre className="modern-code-pre">
          <code>{code}</code>
        </pre>
      )}
    </div>
  )
}

/**
 * 高级现代极简代码块容器组件。
 * 顶部带有精致语言标记与一键复制代码按钮。
 * 对 HTML 代码块支持智能实时渲染预览与代码无缝切换。
 */
function CodeBlock({ language, code }: { language?: string; code: string }) {
  const [copied, setCopied] = useState(false)
  const isHtml = language?.toLowerCase() === 'html' || language?.toLowerCase() === 'htm'
  const isSvg = (language?.toLowerCase() === 'svg' || language?.toLowerCase() === 'xml') && /<svg\b/i.test(code)
  const [selectedView, setViewMode] = useState<'code' | 'preview' | null>(null)
  const viewMode = selectedView ?? (isHtml || isSvg ? 'preview' : 'code')

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    void navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  const highlightedHtml = React.useMemo(() => {
    const rawLang = (language ?? '').toLowerCase().trim()
    const aliasMap: Record<string, string> = {
      js: 'javascript',
      ts: 'typescript',
      py: 'python',
      sh: 'bash',
      shell: 'bash',
      zsh: 'bash',
      yml: 'yaml',
      md: 'markdown'
    }
    const lang = aliasMap[rawLang] || rawLang
    const grammar = Prism.languages[lang]
    if (grammar) {
      try {
        return Prism.highlight(code, grammar, lang)
      } catch {
        return null
      }
    }
    return null
  }, [language, code])

  return (
    <div className={`modern-code-block ${isHtml ? 'is-html' : ''}`}>
      <div className="modern-code-header">
        <div className="modern-code-meta">
          <span className="code-dot red" />
          <span className="code-dot yellow" />
          <span className="code-dot green" />
          <span className="modern-code-lang">{language ? language.toUpperCase() : 'CODE'}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {(isHtml || isSvg) && (
            <div className="preview-toggle-group">
              <button
                type="button"
                className={`preview-toggle-btn ${viewMode === 'preview' ? 'is-active' : ''}`}
                onClick={() => setViewMode('preview')}
                title={isSvg ? '预览 SVG 图表' : '实时交互预览渲染'}
              >
                <Eye size={11} style={{ marginRight: 3, verticalAlign: 'middle' }} />
                {isSvg ? '图表' : '实时预览'}
              </button>
              <button
                type="button"
                className={`preview-toggle-btn ${viewMode === 'code' ? 'is-active' : ''}`}
                onClick={() => setViewMode('code')}
                title={isSvg ? '查看 SVG 源代码' : '查看 HTML 源代码'}
              >
                <Code size={11} style={{ marginRight: 3, verticalAlign: 'middle' }} />
                源码
              </button>
            </div>
          )}
          <button
            type="button"
            className={`modern-code-copy ${copied ? 'is-copied' : ''}`}
            onClick={handleCopy}
            title={copied ? '已复制代码' : '复制代码'}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            <span>{copied ? '已复制' : '复制'}</span>
          </button>
        </div>
      </div>
      {isSvg && viewMode === 'preview' ? (
        <SvgDiagram svg={code} filename="diagram.png" allowSvgDownload />
      ) : isHtml && viewMode === 'preview' ? (
        <iframe
          title="HTML 实时预览"
          srcDoc={code}
          sandbox="allow-scripts allow-modals"
          className="html-preview-frame"
        />
      ) : (
        <pre className="modern-code-pre">
          {highlightedHtml ? (
            <code dangerouslySetInnerHTML={{ __html: highlightedHtml }} />
          ) : (
            <code>{code}</code>
          )}
        </pre>
      )}
    </div>
  )
}

/**
 * Markdown 排版与自定义组件映射器。
 * 支持 GitHub Flavored Markdown (GFM)，并安全重定向超链接与图片协议。
 * 行内代码保持文本，仅显式文件链接支持打开。
 * 使用 React.memo 与 useMemo 深度稳定子组件引用，彻底消除会话滚动过程中的子组件卸载与重绘抖动。
 */
const MessageMarkdown = React.memo(function MessageMarkdown({
  content,
  openArtifact,
  isStreaming = false
}: {
  content: unknown
  openArtifact(path: string): void
  isStreaming?: boolean
}) {
  const rawText = contentText(content)
  const openArtifactRef = useRef(openArtifact)
  openArtifactRef.current = openArtifact

  const components = React.useMemo(() => ({
    table: ({ children }: { children?: React.ReactNode }) => (
      <div className="message-table-scroll" role="region" aria-label="表格（可横向滚动）" tabIndex={0}>
        <table>{children}</table>
      </div>
    ),
    // 只有代码块使用独立容器，行内代码不自动升级为文件组件。
    pre: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    code: ({ className, children, node: _node, ...props }: React.ComponentPropsWithoutRef<'code'> & { node?: unknown }) => {
      const match = /language-(\w+)/.exec(className || '')
      const textContent = String(children)
      // 若为 Mermaid 流程图或时序图代码块
      if (match && match[1].toLowerCase() === 'mermaid') {
        return <MermaidBlock code={textContent.replace(/\n$/, '')} isStreaming={isStreaming} />
      }
      // 若为多行代码或带有明确语言声明的代码块
      if (match || textContent.includes('\n')) {
        return <CodeBlock language={match ? match[1] : ''} code={textContent.replace(/\n$/, '')} />
      }
      return <code className="modern-inline-code" {...props}>{children}</code>
    },
    // 自定义超链接处理：外部链接浏览器打开，交付物/文件链接原生打开与穿透定位
    a: ({ href, children, node: _node, ...props }: React.ComponentPropsWithoutRef<'a'> & { node?: unknown }) => {
      if (!href) return <span {...props}>{children}</span>
      // 检测是否属于本地物理文件或交付物协议
      if (href.startsWith('medpeer-artifact:') || href.startsWith('file://') || isFilePath(href)) {
        let path: string
        try { path = href.startsWith('medpeer-artifact:') ? decodeURIComponent(href.slice('medpeer-artifact:'.length)) : cleanPath(href) }
        catch { return <span>{children}</span> }
        return <a {...props} className="transcript-file-link" href={href} onClick={event => {
          event.preventDefault()
          if (href.startsWith('medpeer-artifact:')) openArtifactRef.current(path)
          else if (path.endsWith('/') || path.endsWith('\\')) void window.medpeer.openWorkspaceFolder(path)
          else void window.medpeer.openWorkspaceFile(path).catch(() => window.medpeer.showItemInFolder(path))
        }}>{children}</a>
      }
      return (
        <a
          {...props}
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          onClick={event => {
            event.preventDefault()
            if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) {
              void window.medpeer.openExternal(href)
            }
          }}
        >
          {children}
        </a>
      )
    },
    // 自定义图片处理：映射至支持成果图片异步提取的 ArtifactImage 组件
    img: ({ src, alt }: { src?: string; alt?: string }) => <ArtifactImage src={src} alt={alt} />
  }), [isStreaming])

  return (
    <div className={`message-markdown-wrap ${isStreaming ? 'is-streaming' : ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={url => {
          // 允许安全的 data:image 图片与 medpeer-artifact 交付物协议
          if (/^data:image\/(png|jpeg|webp);/i.test(url) || url.startsWith('medpeer-artifact:')) return url
          // 允许常规 http(s) 与 mailto 链接
          if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) return url
          // 允许本地文件与工作区相对路径
          if (/^file:\/\//i.test(url) || isFilePath(url)) return url
          return ''
        }}
        components={components}
      >
        {rawText}
      </ReactMarkdown>
    </div>
  )
}, (prev, next) => {
  return prev.content === next.content && prev.isStreaming === next.isStreaming
})

/**
 * 交付成果物折叠卡片组件。
 * 展示产出文件名称、媒体格式，并在展开时异步请求缩略预览图。
 */
function ArtifactCard({ artifact, open }: { artifact: ArtifactRef; open(path: string): void }) {
  const [expanded, setExpanded] = useState(false)
  const [preview, setPreview] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setPreview('')
    setError('')
    if (!expanded || !previewImageTypes.includes(artifact.media_type)) return
    let current = true
    window.medpeer.readArtifact(artifact.relative_path).then(result => {
      if (current && result.media_type === artifact.media_type) {
        setPreview(`data:${result.media_type};base64,${result.data}`)
      }
    }).catch(reason => {
      if (current) setError(String(reason))
    })
    return () => { current = false }
  }, [artifact.id, artifact.media_type, artifact.relative_path, expanded])

  return (
    <details className="artifact-card modern-artifact-card" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
      <summary>
        <span className="artifact-title">
          <strong>{artifact.name}</strong>
        </span>
      </summary>
      {error && <p role="alert">{error}</p>}
      {preview && <img src={preview} alt={artifact.name} />}
      {expanded && (
        <div className="artifact-actions">
          <button className="primary-action-sm" onClick={() => open(artifact.relative_path)}>
            打开文件
          </button>
          <button
            className="subtle-button-sm"
            onClick={() => void window.medpeer.showItemInFolder(artifact.relative_path)}
          >
            在文件夹中显示
          </button>
        </div>
      )}
    </details>
  )
}

/**
 * 主对话流容器组件。
 *
 * 第一步：渲染会话顶部快捷操作条（包含全量会话复制与导出）。
 * 第二步：渲染历史消息列表，提供独立消息一键复制、Markdown 代码与文件智能联动。
 * 第三步：渲染 AI 正在流式生成的文本片段（stream）与精美闪烁游标。
 * 第四步：实时展示后台 LLM 思考过程、工具调用与插件调用卡片。
 * 第五步：渲染关联的附件与交付物成果卡片。
 * 第六步：监听滚动条位置，实现智能近底自动贴合滚动与“回到底部”悬浮按钮。
 */
export function Conversation({ state, openArtifact }: { state: AgentState; openArtifact(path: string): void }) {
  const pane = useRef<HTMLDivElement>(null)
  const [nearBottom, setNearBottom] = useState(true)
  const [allCopied, setAllCopied] = useState(false)
  const isAutoScrolling = useRef(false)

  // 切换会话时重置并强制滚动至底部
  useEffect(() => {
    setNearBottom(true)
    if (pane.current) {
      isAutoScrolling.current = true
      pane.current.scrollTop = pane.current.scrollHeight
      const raf = requestAnimationFrame(() => {
        isAutoScrolling.current = false
      })
      return () => cancelAnimationFrame(raf)
    }
  }, [state.snapshot?.session_id])

  // 流式生成或新消息到达时，若此前处于近底部则自动跟随向下滚动
  useLayoutEffect(() => {
    if (nearBottom && pane.current) {
      isAutoScrolling.current = true
      pane.current.scrollTop = pane.current.scrollHeight
      const raf = requestAnimationFrame(() => {
        isAutoScrolling.current = false
      })
      return () => cancelAnimationFrame(raf)
    }
  }, [state.snapshot, state.stream, state.runLog, nearBottom])

  // 复制整个会话对话记录为 Markdown 纯文本
  const handleCopyFullChat = () => {
    if (!state.snapshot?.messages) return
    const lines: string[] = []
    state.snapshot.messages
      .filter(m => ['human', 'ai'].includes(m.type) && contentText(m.data.content))
      .forEach(m => {
        const role = m.type === 'human' ? '### User' : '### Assistant (MedPeer)'
        lines.push(`${role}\n\n${contentText(m.data.content)}\n`)
      })
    if (state.stream) {
      lines.push(`### Assistant (MedPeer)\n\n${state.stream}\n`)
    }
    void navigator.clipboard.writeText(lines.join('\n---\n\n'))
    setAllCopied(true)
    setTimeout(() => setAllCopied(false), 2000)
  }

  const hasMessages = Boolean(
    (state.snapshot?.messages && state.snapshot.messages.length > 0) ||
    state.stream.length > 0 ||
    state.pendingPrompt
  )

  const isRunning = state.phase === 'running'
  const isStreaming = state.stream.length > 0
  const isThinking = isRunning && state.stream.length === 0
  const activeRun = state.snapshot?.active_run
  const latestRunId = state.runId ?? activeRun?.id ?? state.lastFinishedRunId ?? state.snapshot?.runs.at(-1)?.id
  const artifacts = React.useMemo(() => (state.snapshot?.artifacts ?? []).filter(artifact => latestRunId && artifact.source_run_id === latestRunId), [state.snapshot?.artifacts, latestRunId])
  const hasLiveRun = Boolean(state.runId || (!state.lastRunSaved && (activeRun || state.runLog.length)) || isRunning)
  const activityStatus = state.phase === 'question' || (!state.runId && activeRun && state.phase === 'idle') ? 'paused' : state.phase
  const liveRecords = [...(activeRun?.records ?? []), ...state.runLog]
  const messages = state.snapshot?.messages.filter(message => ['human', 'ai'].includes(message.type) && contentText(message.data.content)) ?? []
  const lastMessageByUser = new Map<string, number>()
  let userMessageId: string | undefined
  messages.forEach((message, index) => {
    if (message.type === 'human') userMessageId = typeof message.data.id === 'string' ? message.data.id : undefined
    if (userMessageId) lastMessageByUser.set(userMessageId, index)
  })
  const savedActivity = (run: Snapshot['runs'][number], index: number) => (
    <div className="saved-run-activity" key={run.id ?? index}>
      <InlineActivity records={run.records} tools={run.tools as Record<string, RunRecord>} status={run.status ?? 'complete'} elapsed={run.elapsed} />
    </div>
  )

  return (
    <div className="conversation-wrap codex-conversation">
      <div
        className="conversation"
        ref={pane}
        onScroll={event => {
          if (isAutoScrolling.current) return
          const node = event.currentTarget
          // 距离底部 100px 以内视为近底状态
          const atBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 100
          setNearBottom(prev => (prev === atBottom ? prev : atBottom))
        }}
      >
        {/* 历史消息列表 */}
        {messages
          .map((message, index) => {
            const textToCopy = contentText(message.data.content)

            return (
              <React.Fragment key={typeof message.data.id === 'string' ? message.data.id : index}>
              {message.type === 'ai' && state.snapshot?.runs.filter(run => run.user_message_id && lastMessageByUser.get(run.user_message_id) === index).map(savedActivity)}
              <article className={`message ${message.type}`} aria-label={message.type === 'human' ? '你' : 'MedPeer'}>
                <div className="message-content">
                  <MessageMarkdown
                    content={message.type === 'human' && Array.isArray(message.data.content) ? message.data.content.slice(0, 1) : message.data.content}
                    openArtifact={openArtifact}
                  />
                </div>
                {/* 复制按钮移至消息气泡正文下方 */}
                <div className={`message-footer-row ${message.type}-footer`}>
                  <div className="message-actions-bar">
                    <CopyButton text={textToCopy} />
                  </div>
                </div>
              </article>
              {message.type === 'human' && state.snapshot?.runs.filter(run => run.user_message_id && lastMessageByUser.get(run.user_message_id) === index).map(savedActivity)}
              </React.Fragment>
            )
          })}

        {/* 待发送/已发送未决提示词气泡 */}
        {state.pendingPrompt && (
          <article className="message human" aria-label="你">
            <div className="message-content">{state.pendingPrompt}</div>
            <div className="message-footer-row human-footer">
              <div className="message-actions-bar">
                <CopyButton text={state.pendingPrompt} />
              </div>
            </div>
          </article>
        )}

        {/* 实时流式响应气泡或思考中气泡 */}
        {(isStreaming || hasLiveRun) && (
          <article className="message ai is-active-response" aria-label="MedPeer" aria-busy={isRunning}>
            {!isStreaming && <div className="message-header-row">
              <div className="message-label streaming-label">
                {state.phase === 'question' ? '等待你的答复' : state.phase === 'stopping' ? '正在停止…' : state.phase === 'disconnected' ? '连接已中断' : state.phase === 'idle' ? '等待继续' : isThinking ? '正在思考…' : '正在回复'}
              </div>
            </div>}

            <InlineActivity records={liveRecords} tools={state.tools} running={isRunning} status={activityStatus} />

            {/* 流式文本正文 */}
            {state.stream.length > 0 && (
              <div className="message-content">
                <MessageMarkdown
                  content={state.stream}
                  openArtifact={openArtifact}
                  isStreaming={isRunning}
                />
              </div>
            )}

            {/* 复制按钮置于流式回复底部 */}
            {state.stream.length > 0 && (
              <div className="message-footer-row ai-footer">
                <div className="message-actions-bar">
                  <CopyButton text={state.stream} label="复制" />
                </div>
              </div>
            )}
          </article>
        )}

        {state.snapshot?.runs.filter(run => !run.user_message_id || !lastMessageByUser.has(run.user_message_id)).map(savedActivity)}

        {/* 仅展示最新一轮生成的交付文件。 */}
        {artifacts.map(artifact => (
          <ArtifactCard key={artifact.id} artifact={artifact} open={openArtifact} />
        ))}

        {/* 底部全量对话操作栏 */}
        {hasMessages && (
          <div className="conversation-bottom-toolbar">
            <button
              type="button"
              className={`conv-toolbar-btn ${allCopied ? 'is-copied' : ''}`}
              onClick={handleCopyFullChat}
              title="复制当前会话的全部对话记录"
            >
              {allCopied ? <Check size={12} /> : <Copy size={12} />}
              <span>{allCopied ? '全量记录已复制' : '复制全部记录'}</span>
            </button>
            {state.snapshot?.session_id && (
              <button
                type="button"
                className="conv-toolbar-btn"
                onClick={() => void window.medpeer.exportSession(state.snapshot!.session_id)}
                title="导出当前会话为本地文本"
              >
                <Download size={12} />
                <span>导出会话</span>
              </button>
            )}
          </div>
        )}
      </div>

      {/* 用户向上翻阅时呈现“回到底部”悬浮按钮 */}
      {!nearBottom && (
        <button
          className="back-bottom modern-back-bottom"
          aria-label="回到底部"
          title="回到底部"
          onClick={() => pane.current?.scrollTo({ top: pane.current.scrollHeight, behavior: 'smooth' })}
        >
          <ArrowDown size={18} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
