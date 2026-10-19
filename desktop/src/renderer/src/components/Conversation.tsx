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
import { Check, Copy, Download, Layers } from 'lucide-react'
import type { AgentState } from '../state/agent-state'
import type { ArtifactRef, Snapshot, RunRecord } from '../../../shared/protocol'
import { FileLink, isFilePath } from './FileLink'
import { InlineActivity } from './InlineActivity'
import medpeerIcon from '../../../../../assets/medpeer.ico?url'

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

/**
 * 高级现代极简代码块容器组件。
 * 顶部带有精致语言标记与一键复制代码按钮。
 */
function CodeBlock({ language, code }: { language?: string; code: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    void navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  return (
    <div className="modern-code-block">
      <div className="modern-code-header">
        <span className="modern-code-lang">{language ? language.toUpperCase() : 'CODE'}</span>
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
      <pre className="modern-code-pre">
        <code>{code}</code>
      </pre>
    </div>
  )
}

/**
 * Markdown 排版与自定义组件映射器。
 * 支持 GitHub Flavored Markdown (GFM)，并安全重定向超链接与图片协议。
 * 自动识别文件路径转换为可直接点击穿透打开的交互式胶囊。
 */
function MessageMarkdown({
  content,
  openArtifact,
  isStreaming = false
}: {
  content: unknown
  openArtifact(path: string): void
  isStreaming?: boolean
}) {
  const rawText = contentText(content)

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
        components={{
          table: ({ children }) => (
            <div className="message-table-scroll" role="region" aria-label="表格（可横向滚动）" tabIndex={0}>
              <table>{children}</table>
            </div>
          ),
          // 自定义代码块与行内代码：行内文件路径转换为 FileLink，多行代码块添加复制按钮与语言头部
          pre: ({ children }) => <>{children}</>,
          code: ({ className, children, ...props }) => {
            const match = /language-(\w+)/.exec(className || '')
            const textContent = String(children)
            // 若为多行代码或带有明确语言声明的代码块
            if (match || textContent.includes('\n')) {
              return <CodeBlock language={match ? match[1] : ''} code={textContent.replace(/\n$/, '')} />
            }
            // 若为行内代码且符合文件或目录路径特征，渲染为高颜值文件胶囊
            const trimmed = textContent.trim()
            if (isFilePath(trimmed)) {
              return <FileLink path={trimmed} inline openArtifact={openArtifact} />
            }
            return <code className="modern-inline-code" {...props}>{children}</code>
          },
          // 自定义超链接处理：外部链接浏览器打开，交付物/文件链接原生打开与穿透定位
          a: ({ href, children, ...props }) => {
            if (!href) return <span {...props}>{children}</span>
            // 检测是否属于本地物理文件或交付物协议
            if (href.startsWith('medpeer-artifact:') || href.startsWith('file://') || isFilePath(href)) {
              return (
                <FileLink
                  path={href}
                  title={typeof children === 'string' ? children : undefined}
                  openArtifact={openArtifact}
                />
              )
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
          img: ({ src, alt }) => <ArtifactImage src={src} alt={alt} />
        }}
      >
        {rawText}
      </ReactMarkdown>
      {/* 实时流式输出闪烁游标 */}
      {isStreaming && <span className="streaming-cursor" aria-hidden="true" />}
    </div>
  )
}

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
          <Layers size={13} className="artifact-icon" />
          <strong>{artifact.name}</strong>
        </span>
        <small>{artifact.media_type}</small>
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

  // 切换会话时重置并强制滚动至底部
  useEffect(() => {
    setNearBottom(true)
    pane.current?.scrollTo({ top: pane.current.scrollHeight })
  }, [state.snapshot?.session_id])

  // 流式生成或新消息到达时，若此前处于近底部则自动跟随向下滚动
  useLayoutEffect(() => {
    if (nearBottom) pane.current?.scrollTo({ top: pane.current.scrollHeight })
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
      <InlineActivity records={run.records} tools={run.tools as Record<string, RunRecord>} status={run.status ?? 'complete'} />
    </div>
  )

  return (
    <div className="conversation-wrap">
      <div
        className="conversation"
        ref={pane}
        onScroll={event => {
          const node = event.currentTarget
          // 距离底部 100px 以内视为近底状态
          setNearBottom(node.scrollHeight - node.scrollTop - node.clientHeight < 100)
        }}
      >
        {/* 历史消息列表 */}
        {messages
          .map((message, index) => {
            const textToCopy = contentText(message.data.content)

            return (
              <React.Fragment key={typeof message.data.id === 'string' ? message.data.id : index}>
              <article className={`message ${message.type}`} aria-label={message.type === 'human' ? '你' : 'MedPeer'}>
                {message.type === 'ai' && <img className="message-avatar" src={medpeerIcon} alt="MedPeer" />}
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
              {state.snapshot?.runs.filter(run => run.user_message_id && lastMessageByUser.get(run.user_message_id) === index).map(savedActivity)}
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
            <img className="message-avatar" src={medpeerIcon} alt="MedPeer" />
            <div className="message-header-row">
              <div className="message-label streaming-label">
                <span className="streaming-pulse-dot" />
                {state.phase === 'question' ? '等待你的答复' : state.phase === 'stopping' ? '正在停止…' : state.phase === 'disconnected' ? '连接已中断' : state.phase === 'idle' ? '等待继续' : isThinking ? '正在思考…' : '正在回复'}
              </div>
            </div>

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

            {/* 对话内联工具调用、插件执行与 LLM 后台动作展示 */}
            <InlineActivity
              records={liveRecords}
              tools={state.tools}
              running={isRunning}
              status={activityStatus}
            />

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

        {/* 会话附件列表 */}
        {state.snapshot?.attachments.map(attachment => (
          <button
            className="attachment-chip modern-attachment-chip"
            key={attachment.id}
            onClick={() => void window.medpeer.openAttachment(attachment.id)}
          >
            {attachment.name}
          </button>
        ))}

        {/* 交付物卡片列表 */}
        {state.snapshot?.artifacts.map(artifact => (
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
          onClick={() => pane.current?.scrollTo({ top: pane.current.scrollHeight, behavior: 'smooth' })}
        >
          回到底部
        </button>
      )}
    </div>
  )
}
