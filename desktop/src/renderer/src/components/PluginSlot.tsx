/**
 * @file PluginSlot.tsx
 * @description 动态插件视图容器插槽组件。
 * 负责根据已加载的插件注册表（pluginViews），动态渲染各插件专属的交互与呈现组件，
 * 并向下分发对应的插件状态、交互问题、运行事件流水及交互回调。
 * @author tongliga
 * @date 2026-10-16
 */

import type { PluginDescriptor, Question, RunRecord } from '../../../shared/protocol'
import { pluginViews } from '../plugins'
import type { PluginState } from '../plugins/types'

/**
 * 插件插槽组件属性接口。
 */
type PluginSlotProps = {
  /** 当前启用的插件描述符列表 */
  plugins: PluginDescriptor[]
  /** 各插件专属的状态快照字典（以 plugin_id 为键） */
  states: Record<string, PluginState>
  /** 处于等待答复状态的交互提问对象（无提问时为 null） */
  question: Question | null
  /** 本轮任务产生的事件流水列表 */
  events: RunRecord[]
  /** 最近一轮运行是否已完成并持久化保存 */
  saved: boolean
  /** 当前运行轮次 ID */
  runId: string | null
  /** 提交交互提问回答的回调函数 */
  answer(runId: string, interactionId: string, text: string): void
  /** 打开交付物文件的回调函数 */
  open(path: string): void
}

/**
 * 动态插件插槽渲染组件。
 *
 * @param props - 组件属性
 */
export function PluginSlot({ plugins, states, question, events, saved, runId, answer, open }: PluginSlotProps) {
  return <>
    {plugins.map(plugin => {
      const View = pluginViews[plugin.id]
      return View ? (
        <View
          key={plugin.id}
          state={states[plugin.id] ?? null}
          question={question}
          events={events}
          saved={saved}
          runId={runId}
          answer={answer}
          open={open}
        />
      ) : null
    })}
  </>
}
