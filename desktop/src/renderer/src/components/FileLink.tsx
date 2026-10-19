/**
 * @file FileLink.tsx
 * @description 交互式文件与目录链接组件。
 * 将文本中的文件路径、代码引用与目录识别为可直接点击穿透的交互式胶囊标签。
 * 支持系统默认程序打开文件、资源管理器高亮定位、以及目录直接穿透跳转。
 * @author tongliga
 * @date 2026-10-19
 */

import React, { useState } from 'react'
import {
  File,
  FileCode,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderOpen,
  Copy,
  Check
} from 'lucide-react'

/** 常见代码文件扩展名 */
const codeExtensions = new Set([
  'ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs', 'py', 'pyw', 'go', 'rs', 'java', 'c', 'cpp',
  'h', 'hpp', 'cs', 'rb', 'php', 'swift', 'kt', 'sql', 'sh', 'bash', 'zsh', 'bat',
  'cmd', 'ps1', 'json', 'yaml', 'yml', 'toml', 'xml', 'html', 'css', 'scss', 'sass', 'vue'
])

/** 常见表格与数据扩展名 */
const sheetExtensions = new Set(['csv', 'tsv', 'xlsx', 'xls', 'parquet'])

/** 常见文档文件扩展名 */
const docExtensions = new Set(['md', 'markdown', 'txt', 'pdf', 'doc', 'docx', 'rtf', 'log'])

/** 常见图片扩展名 */
const imageExtensions = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico'])

/**
 * 判断指定字符串是否为文件或目录路径。
 *
 * @param text - 待检测的文本字符串
 * @returns 是否符合文件或目录路径特征
 */
export function isFilePath(text: string): boolean {
  if (!text || typeof text !== 'string') return false
  const trimmed = text.trim()
  if (trimmed.length < 2 || trimmed.length > 260) return false
  if (trimmed.includes('\n')) return false

  // 以 file:// 或 medpeer-artifact: 开头
  if (trimmed.startsWith('file://') || trimmed.startsWith('medpeer-artifact:')) return true

  // 绝对路径（Windows 驱动器如 C:\ 或 Unix 绝对路径 /）
  if (/^[a-zA-Z]:[/\\]/.test(trimmed) || trimmed.startsWith('/')) {
    return true
  }

  // 相对路径（以 ./ 或 ../ 开头，或包含目录分隔符）
  if (trimmed.startsWith('./') || trimmed.startsWith('../')) return true

  // 检测常见扩展名
  const lastDot = trimmed.lastIndexOf('.')
  if (lastDot > 0 && lastDot < trimmed.length - 1) {
    const ext = trimmed.slice(lastDot + 1).toLowerCase().split(/[?#:]/)[0]
    if (codeExtensions.has(ext) || sheetExtensions.has(ext) || docExtensions.has(ext) || imageExtensions.has(ext)) {
      return true
    }
  }

  // 以常见目录名开头的相对路径（如 src/, docs/, tests/, packages/ 等）
  if (/^(src|packages|desktop|tests|scripts|docs|doc|openwiki|assets|\.github)[/\\]/i.test(trimmed)) {
    return true
  }

  // 明确以斜杠结尾的目录路径
  if (trimmed.endsWith('/') || trimmed.endsWith('\\')) {
    return trimmed.includes('/') || trimmed.includes('\\')
  }

  return false
}

/**
 * 清理路径字符串，去除 file:// 前缀与可能附带的行号锚点。
 */
export function cleanPath(raw: string): string {
  let p = raw.trim()
  if (p.startsWith('file:///')) {
    p = decodeURIComponent(p.slice('file:///'.length))
  } else if (p.startsWith('file://')) {
    p = decodeURIComponent(p.slice('file://'.length))
  }
  // 去除 #L10-L20 或 :12:34 等行号定位后缀
  p = p.replace(/#L\d+(-L\d+)?$/i, '').replace(/:\d+(:\d+)?$/, '')
  return p
}

/**
 * 获取路径对应的类型图标。
 */
function getPathIcon(path: string, isDir: boolean) {
  if (isDir || path.endsWith('/') || path.endsWith('\\')) {
    return <Folder size={12} className="file-chip-icon dir" />
  }
  const ext = path.split('.').at(-1)?.toLowerCase().split(/[?#:]/)[0] ?? ''
  if (codeExtensions.has(ext)) {
    return <FileCode size={12} className="file-chip-icon code" />
  }
  if (sheetExtensions.has(ext)) {
    return <FileSpreadsheet size={12} className="file-chip-icon sheet" />
  }
  if (docExtensions.has(ext)) {
    return <FileText size={12} className="file-chip-icon doc" />
  }
  return <File size={12} className="file-chip-icon generic" />
}

export interface FileLinkProps {
  /** 路径字符串（相对路径或绝对路径） */
  path: string
  /** 可选展示名称，未指定时展示路径末段文件名或相对路径 */
  title?: string
  /** 是否内联显示在文本中 */
  inline?: boolean
  /** 打开交付物成果的回调（若属于 medpeer-artifact） */
  openArtifact?(relativePath: string): void
}

/**
 * 现代极简风格交互式文件/目录胶囊组件。
 * 点击直接在本地系统关联应用中打开，悬浮显示在资源管理器定位按钮。
 */
export function FileLink({ path, title, inline = true, openArtifact }: FileLinkProps) {
  const [copied, setCopied] = useState(false)
  const cleaned = cleanPath(path)
  const isDirectory = cleaned.endsWith('/') || cleaned.endsWith('\\') || (!cleaned.includes('.') && cleaned.includes('/'))
  const fileName = cleaned.split(/[/\\]/).filter(Boolean).at(-1) ?? cleaned
  const displayTitle = title || fileName

  const handleOpen = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()

    if (path.startsWith('medpeer-artifact:') && openArtifact) {
      openArtifact(decodeURIComponent(path.slice('medpeer-artifact:'.length)))
      return
    }

    if (window.medpeer) {
      if (isDirectory) {
        void window.medpeer.openWorkspaceFolder(cleaned)
      } else {
        void window.medpeer.openWorkspaceFile(cleaned).catch(() => {
          void window.medpeer.showItemInFolder(cleaned)
        })
      }
    }
  }

  const handleReveal = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (window.medpeer) {
      void window.medpeer.showItemInFolder(cleaned)
    }
  }

  const handleCopyPath = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    void navigator.clipboard.writeText(cleaned)
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }

  return (
    <span
      className={`file-link-chip ${inline ? 'is-inline' : 'is-block'} ${isDirectory ? 'is-dir' : 'is-file'}`}
      title={cleaned}
    >
      <button type="button" className="file-chip-main" onClick={handleOpen} aria-label={`打开 ${displayTitle}`}>
        {getPathIcon(cleaned, isDirectory)}
        <span className="file-chip-name">{displayTitle}</span>
      </button>
      <span className="file-chip-actions" onClick={e => e.stopPropagation()}>
        <button
          type="button"
          className="file-chip-btn"
          title="在资源管理器中定位"
          aria-label="在资源管理器中定位"
          onClick={handleReveal}
        >
          <FolderOpen size={11} />
        </button>
        <button
          type="button"
          className="file-chip-btn"
          title={copied ? '已复制路径' : '复制路径'}
          aria-label="复制路径"
          onClick={handleCopyPath}
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
        </button>
      </span>
    </span>
  )
}
