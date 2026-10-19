/**
 * @file index.ts
 * @description 客户端前端扩展插件注册总表。
 * 导出已注册插件 ID 与对应 React 视图组件（如科研数据分析 data_analysis）的映射字典。
 * @author tongliga
 * @date 2026-10-16
 */

import type { ComponentType } from 'react'
import { DataAnalysis } from './data_analysis'
import type { PluginViewProps } from './types'

/**
 * 已加载插件与渲染视图组件的全局映射表。
 */
export const pluginViews: Record<string, ComponentType<PluginViewProps>> = {
  data_analysis: DataAnalysis
}
