/**
 * @file AttachmentChips.tsx
 * @description 待发送对话附件标签组件。
 * 负责在任务输入框上方呈现用户已选择的附件列表，自动加载图片类型的 base64 缩略图预览，
 * 显示文件大小（KB），并支持单项快捷移除。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useState } from 'react'
import { Paperclip, X } from 'lucide-react'
import type { AttachmentRef } from '../../../shared/protocol'

/**
 * 附件标签组件属性接口。
 */
type AttachmentChipsProps = {
  /** 当前待发送的附件引用列表 */
  attachments: AttachmentRef[]
  /** 移除指定附件的回调函数 */
  remove(id: string): void
  /** 发生读取异常时的错误通知回调 */
  onError(message: string): void
}

/**
 * 对话待发送附件缩略标签栏组件。
 *
 * 第一步：筛选类型为 image/* 的图片附件，发起异步 readAttachment 提取缩略图。
 * 第二步：将读取到的 base64 转换为 data URL 并缓存至 previews 状态中。
 * 第三步：渲染标签徽章，包含图标/缩略图、文件名、KB 大小及删除按钮。
 *
 * @param props - 组件属性
 */
export function AttachmentChips({ attachments, remove, onError }: AttachmentChipsProps) {
  const [previews, setPreviews] = useState<Record<string, string>>({})

  // 异步加载图片类附件的预览图
  useEffect(() => {
    let current = true
    Promise.all(attachments.filter(file => file.media_type.startsWith('image/')).map(async file => {
      const content = await window.medpeer.readAttachment(file.id)
      if (content.media_type !== file.media_type || !content.media_type.startsWith('image/')) {
        return [file.id, ''] as const
      }
      return [file.id, `data:${file.media_type};base64,${content.data}`] as const
    })).then(entries => {
      if (current) setPreviews(Object.fromEntries(entries))
    }).catch(reason => {
      if (current) {
        setPreviews({})
        onError(String(reason))
      }
    })

    return () => { current = false }
  }, [attachments])

  return <div className="attachment-chips" aria-label="待发送附件">
    {attachments.map(file => <span className="attachment-chip" key={file.id}>
      {previews[file.id] ? <img src={previews[file.id]} alt="" /> : <Paperclip size={13} />}
      <span>{file.name}</span>
      <small>{Math.ceil(file.size / 1024)} KB</small>
      <button aria-label={`移除 ${file.name}`} onClick={() => remove(file.id)}>
        <X size={13} />
      </button>
    </span>)}
  </div>
}
