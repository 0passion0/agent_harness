/**
 * @file types.ts
 * @description 客户端插件组件通用属性与状态接口契约。
 * 定义任意扩展插件在渲染插槽（PluginSlot）中所需的上下文、事件、提问与回调类型。
 * @author tongliga
 * @date 2026-10-16
 */

import type { Json, Question, RunRecord } from '../../../shared/protocol'

/**
 * 单个插件持久化状态快照类型（键值 JSON 对象）。
 */
export type PluginState = Record<string, Json>

/**
 * 嵌入到插件插槽（PluginSlot）中的插件视图组件标准 Props 接口契约。
 */
export type PluginViewProps = {
  /** 当前插件的最新运行时状态快照（未初始化时为 null） */
  state: PluginState | null
  /** 当前待处理的提问/授权请求 */
  question: Question | null
  /** 本轮次产生的所有执行事件流水 */
  events: RunRecord[]
  /** 最近一轮执行是否已完成持久化保存 */
  saved: boolean
  /** 当前运行轮次 ID */
  runId: string | null
  /** 提交交互提问回答的回调函数 */
  answer(runId: string, interactionId: string, text: string): void
  /** 打开交付物成果文件的回调函数 */
  open(path: string): void
}
