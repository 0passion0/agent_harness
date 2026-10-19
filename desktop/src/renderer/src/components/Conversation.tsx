/**
 * @file Conversation.tsx
 * @description 智能体对话消息流与交付物呈现组件。
 * 负责渲染会话历史消息（包含 Human 提示词与 AI 响应）、实时流式文本生成效果、
 * Markdown 格式化排版、成果图片预览、交付物交互卡片以及智能近底滚动控制。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AgentState } from '../state/agent-state'
import type { ArtifactRef } from '../../../shared/protocol'
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
function contentText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map(block => typeof block === 'object' && block !== null && 'text' in block && typeof block.text === 'string' ? block.text : '')
      .join('\n')
  }
  return JSON.stringify(content) ?? ''
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
 * Markdown 排版与自定义组件映射器。
 * 支持 GitHub Flavored Markdown (GFM)，并安全重定向超链接与图片协议。
 */
function MessageMarkdown({ content, openArtifact }: { content: unknown; openArtifact(path: string): void }) {
  return <ReactMarkdown
    remarkPlugins={[remarkGfm]}
    urlTransform={url => {
      // 允许安全的 data:image 图片与 medpeer-artifact 交付物协议
      if (/^data:image\/(png|jpeg|webp);/i.test(url) || url.startsWith('medpeer-artifact:')) return url
      // 允许常规 http(s) 与 mailto 链接
      if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) return url
      return ''
    }}
    components={{
      // 自定义超链接处理：外部链接使用原生浏览器打开，交付物链接调用 openArtifact
      a: ({ href, children, ...props }) => <a
        {...props}
        href={href}
        onClick={event => {
          event.preventDefault()
          if (!href) return
          if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) {
            void window.medpeer.openExternal(href)
          } else if (href.startsWith('medpeer-artifact:')) {
            openArtifact(decodeURIComponent(href.slice('medpeer-artifact:'.length)))
          }
        }}
      >
        {children}
      </a>,
      // 自定义图片处理：映射至支持成果图片异步提取的 ArtifactImage 组件
      img: ({ src, alt }) => <ArtifactImage src={src} alt={alt} />
    }}
  >
    {contentText(content)}
  </ReactMarkdown>
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

  return <details className="artifact-card" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>
      {artifact.name} <small>{artifact.media_type}</small>
    </summary>
    {error && <p role="alert">{error}</p>}
    {preview && <img src={preview} alt={artifact.name} />}
    {expanded && <button onClick={() => open(artifact.relative_path)}>打开文件</button>}
  </details>
}

/**
 * 主对话流容器组件。
 *
 * 第一步：渲染会话中的历史对话气泡，按角色区分 Human 与 AI。
 * 第二步：渲染用户正在等待响应的待决提示词（pendingPrompt）。
 * 第三步：渲染 AI 正在流式生成的文本片段（stream）。
 * 第四步：渲染关联的附件与交付物成果卡片。
 * 第五步：监听滚动条位置，实现智能近底自动贴合滚动与“回到底部”悬浮按钮。
 */
export function Conversation({ state, openArtifact }: { state: AgentState; openArtifact(path: string): void }) {
  const pane = useRef<HTMLDivElement>(null)
  const [nearBottom, setNearBottom] = useState(true)

  // 切换会话时重置并强制滚动至底部
  useEffect(() => {
    setNearBottom(true)
    pane.current?.scrollTo({ top: pane.current.scrollHeight })
  }, [state.snapshot?.session_id])

  // 流式生成或新消息到达时，若此前处于近底部则自动跟随向下滚动
  useEffect(() => {
    if (nearBottom) pane.current?.scrollTo({ top: pane.current.scrollHeight })
  }, [state.snapshot, state.stream, nearBottom])

  return <div className="conversation-wrap">
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
      {state.snapshot?.messages
        .filter(message => ['human', 'ai'].includes(message.type) && contentText(message.data.content))
        .map((message, index) => <article className={`message ${message.type}`} key={index}>
          {message.type === 'ai' && <img className="message-avatar" src={medpeerIcon} alt="MedPeer" />}
          <div className="message-label">{message.type === 'human' ? '你' : 'MedPeer'}</div>
          <div className="message-content">
            <MessageMarkdown
              content={message.type === 'human' && Array.isArray(message.data.content) ? message.data.content.slice(0, 1) : message.data.content}
              openArtifact={openArtifact}
            />
          </div>
        </article>)}

      {/* 待发送/已发送未决提示词气泡 */}
      {state.pendingPrompt && <article className="message human">
        <div className="message-label">你</div>
        <div className="message-content">{state.pendingPrompt}</div>
      </article>}

      {/* 实时流式响应气泡 */}
      {state.stream.length > 0 && <article className="message ai">
        <img className="message-avatar" src={medpeerIcon} alt="MedPeer" />
        <div className="message-label">正在回复</div>
        <div className="message-content">
          <MessageMarkdown content={state.stream} openArtifact={openArtifact} />
        </div>
      </article>}

      {/* 会话附件列表 */}
      {state.snapshot?.attachments.map(attachment => (
        <button
          className="attachment-chip"
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
    </div>

    {/* 用户向上翻阅时呈现“回到底部”悬浮按钮 */}
    {!nearBottom && (
      <button
        className="back-bottom"
        onClick={() => pane.current?.scrollTo({ top: pane.current.scrollHeight, behavior: 'smooth' })}
      >
        回到底部
      </button>
    )}
  </div>
}
