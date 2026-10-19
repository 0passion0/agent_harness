/**
 * @file Settings.tsx
 * @description 科研数据分析插件专属设置子面板组件。
 * 负责配置数据分析插件的启用状态、独立路由模型（Model/BaseURL/APIKey）、
 * 以及自定义的 Python 和 Node.js 执行解释器绝对路径。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useState } from 'react'
import type { PluginSettingsProps } from '../settings'

/**
 * 科研数据分析插件配置面板组件。
 *
 * 第一步：根据传入的 plugin 配置同步初始化受控表单状态。
 * 第二步：提供保存插件配置回调，并在保存后重置敏感的 API Key 输入缓存。
 * 第三步：提供“打开工作台”按钮，通过自定义事件通知 App 外壳切入研究详情页。
 *
 * @param props - 插件设置组件标准属性
 */
export function DataAnalysisSettings({ plugin, busy, configure }: PluginSettingsProps) {
  const [enabled, setEnabled] = useState(plugin.enabled)
  const [routerModel, setRouterModel] = useState(plugin.configuration?.router.model ?? '')
  const [routerUrl, setRouterUrl] = useState(plugin.configuration?.router.base_url ?? '')
  const [routerKey, setRouterKey] = useState('')
  const [python, setPython] = useState(plugin.configuration?.runtime.python ?? '')
  const [node, setNode] = useState(plugin.configuration?.runtime.node ?? '')

  // 外部配置变动时同步更新本地表单
  useEffect(() => {
    setEnabled(plugin.enabled)
    setRouterModel(plugin.configuration?.router.model ?? '')
    setRouterUrl(plugin.configuration?.router.base_url ?? '')
    setPython(plugin.configuration?.runtime.python ?? '')
    setNode(plugin.configuration?.runtime.node ?? '')
  }, [plugin])

  /** 保存插件配置 */
  function save() {
    void configure({
      id: plugin.id,
      enabled,
      router: { model: routerModel, base_url: routerUrl, api_key: routerKey },
      runtime: { python, node }
    })
    setRouterKey('')
  }

  return <>
    <label className="switch-row">
      <input type="checkbox" checked={enabled} disabled={busy} onChange={e => setEnabled(e.target.checked)} />
      启用科研分析插件
    </label>
    <div className="form-grid">
      <label>路由模型<input value={routerModel} onChange={e => setRouterModel(e.target.value)} /></label>
      <label>路由 API 地址<input value={routerUrl} onChange={e => setRouterUrl(e.target.value)} /></label>
      <label>路由 API 密钥<input type="password" autoComplete="new-password" value={routerKey} onChange={e => setRouterKey(e.target.value)} placeholder="留空保持已保存的密钥" /></label>
      <label>Python 路径<input value={python} onChange={e => setPython(e.target.value)} /></label>
      <label>Node 路径<input value={node} onChange={e => setNode(e.target.value)} /></label>
    </div>
    <div className="settings-actions">
      <button disabled={busy} onClick={save}>保存插件设置</button>
      <button
        disabled={busy || !plugin.loaded}
        onClick={() => window.dispatchEvent(new CustomEvent('medpeer:open-workbench', { detail: plugin.id }))}
      >
        打开工作台
      </button>
    </div>
  </>
}
