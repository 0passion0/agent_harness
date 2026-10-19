/**
 * @file PluginList.tsx
 * @description 插件目录列表展示组件。
 * 负责展示当前可用插件的状态（已加载、已启用、已停用、需配置）及其功能描述，
 * 并提供跳转至对应插件或全局设置面板的操作按钮。
 * @author tongliga
 * @date 2026-10-16
 */

import type { AppPlugin } from '../../../shared/protocol'

/**
 * 插件目录列表组件属性接口。
 */
type PluginListProps = {
  /** 应用当前已注册的插件列表 */
  plugins: AppPlugin[]
  /** 点击配置或管理插件时的回调函数 */
  onOpenSettings(id: string): void
}

/**
 * 插件状态与概要列表视图。
 *
 * @param props - 组件属性
 */
export function PluginList({ plugins, onOpenSettings }: PluginListProps) {
  return <section className="side-section">
    <h3>插件目录</h3>
    {plugins.map(plugin => <div className="plugin-item" key={plugin.id}>
      <div className="plugin-item-heading">
        <strong>{plugin.name}</strong>
        <span className={`plugin-status ${plugin.enabled ? 'enabled' : ''}`}>
          {plugin.enabled ? (plugin.loaded ? '已加载' : '已启用') : (plugin.configured ? '已停用' : '需要配置')}
        </span>
      </div>
      <p>{plugin.description}</p>
      <button className="plugin-settings" onClick={() => onOpenSettings(plugin.id)}>
        {plugin.id === 'user_config' ? '管理模型与设置' : '配置插件'}
      </button>
    </div>)}
    {!plugins.length && <p className="muted">插件目录加载中</p>}
  </section>
}
