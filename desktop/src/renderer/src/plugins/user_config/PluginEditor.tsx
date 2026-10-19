/**
 * @file PluginEditor.tsx
 * @description 开发者模式下的数据分析插件源码与提示词编辑组件。
 * 允许在开发环境下直接查看、编辑与调试 data_analysis 插件下的 prompts/（提示词）、
 * guides/（分析指南）及源码文件，提供未保存状态检测与导出 ZIP 包功能。
 * @author tongliga
 * @date 2026-10-16
 */

import { useState } from 'react'

/**
 * 插件源码编辑器属性接口。
 */
type PluginEditorProps = {
  /** 系统是否处于运行忙碌状态 */
  busy: boolean
  /** 封装的异步操作与反馈提示执行回调 */
  run(action: () => Promise<unknown>, message: string): Promise<void>
  /** 脏检查回调（通知外层是否存在未保存修改） */
  onDirty(dirty: boolean): void
}

/**
 * 数据分析插件代码与提示词编辑器组件。
 *
 * 第一步：调用 plugin.files 加载插件内所有可读写文本文件列表。
 * 第二步：下拉切换文件并通过 plugin.read 提取正文，同步进入受控编辑状态。
 * 第三步：进行实时脏检查（content !== saved），防止未保存切换丢失修改。
 * 第四步：保存时调用 plugin.save 将更改写回本地，并重置脏状态。
 *
 * @param props - 组件属性
 */
export function PluginEditor({ busy, run, onDirty }: PluginEditorProps) {
  const [files, setFiles] = useState<string[]>([])
  const [path, setPath] = useState('')
  const [content, setContent] = useState('')
  const [saved, setSaved] = useState('')
  const [pending, setPending] = useState(false)
  const [exportPath, setExportPath] = useState('')

  const dirty = content !== saved
  const disabled = busy || pending

  /** 封装内部异步操作过程 */
  async function perform(action: () => Promise<unknown>, message: string) {
    setPending(true)
    await run(action, message)
    setPending(false)
  }

  /** 读取指定文件的正文内容 */
  async function read(nextPath: string) {
    const result = await window.medpeer.command({
      id: crypto.randomUUID(),
      method: 'plugin.read',
      params: { plugin_id: 'data_analysis', path: nextPath }
    }) as { content: string }
    setPath(nextPath)
    setContent(result.content)
    setSaved(result.content)
    onDirty(false)
  }

  return <section className="pref-section">
    <h2>数据分析插件开发</h2>
    <p className="pref-desc">在 prompts/ 编辑各流程提示词，guides/ 编辑分析规范，也可编辑插件源码。保存后从下一个任务开始生效。</p>
    <div className="pref-actions">
      <button
        disabled={disabled || dirty}
        onClick={() => void perform(async () => {
          const result = await window.medpeer.command({
            id: crypto.randomUUID(),
            method: 'plugin.files',
            params: { plugin_id: 'data_analysis' }
          }) as { files: string[] }
          setFiles(result.files)
        }, '插件文件列表已加载')}
      >
        加载文件
      </button>
      <button
        disabled={disabled || dirty}
        onClick={() => void perform(async () => {
          const result = await window.medpeer.command({
            id: crypto.randomUUID(),
            method: 'plugin.export',
            params: { plugin_id: 'data_analysis' }
          }) as { path: string }
          setExportPath(result.path)
        }, '插件 ZIP 已导出')}
      >
        导出 ZIP
      </button>
    </div>

    {exportPath && <p className="path-note" role="status">{exportPath}</p>}

    {files.length > 0 && (
      <label>
        插件文件{' '}
        <select
          aria-label="插件文件"
          value={path}
          disabled={disabled || dirty}
          onChange={e => void perform(() => read(e.target.value), '插件文件已读取')}
        >
          <option value="" disabled>选择文件</option>
          {files.map(file => <option key={file} value={file}>{file}</option>)}
        </select>
      </label>
    )}

    {path && <>
      <textarea
        className="pref-editor"
        aria-label={path}
        spellCheck={false}
        disabled={disabled}
        value={content}
        onChange={e => {
          setContent(e.target.value)
          onDirty(e.target.value !== saved)
        }}
      />
      <div className="pref-actions">
        <button
          className="pref-primary"
          disabled={disabled || !dirty}
          onClick={() => void perform(async () => {
            await window.medpeer.command({
              id: crypto.randomUUID(),
              method: 'plugin.save',
              params: { plugin_id: 'data_analysis', path, content }
            })
            setSaved(content)
            onDirty(false)
          }, '插件文件已保存')}
        >
          保存文件
        </button>
        <button
          disabled={disabled || !dirty}
          onClick={() => {
            setContent(saved)
            onDirty(false)
          }}
        >
          撤销更改
        </button>
        {dirty && <span role="status">有未保存更改，请保存或撤销后切换。</span>}
      </div>
    </>}
  </section>
}
