/**
 * @file settings.tsx
 * @description 用户偏好设置面板与插件配置注册表。
 * 定义主设置视图所需的能力委托接口（如模型配置、MCP 调试、Skills 导入与测试）及设置组件注册表。
 * @author tongliga
 * @date 2026-10-16
 */

import type { ComponentType } from 'react'
import type { ApplicationState, ConfigScope, ConfigView } from '../../../shared/protocol'
import { UserConfig } from './user_config'

/**
 * 用户偏好与系统设置主面板组件属性接口。
 */
export type UserSettingsPageProps = {
  /** 初始激活展示的设置页签 */
  initialTab?: 'models' | 'tools' | 'agent' | 'mcp' | 'plugins' | 'profile' | 'credits' | 'usage'
  /** 当前应用生命周期状态快照 */
  application: ApplicationState | null
  /** 当前拉取到的 Agent 指令、技能及 MCP 配置视图 */
  config: ConfigView | null
  /** 系统是否处于运行忙碌状态 */
  busy: boolean
  /** 应用全局系统级别配置变更方法重载 */
  apply(method: 'model.test' | 'exa.clear', params: Record<string, never>): Promise<void>
  apply(method: 'model.configure', params: { model_code: string }): Promise<void>
  apply(method: 'context.configure', params: { max_tokens: string }): Promise<void>
  apply(method: 'exa.configure', params: { api_key: string }): Promise<void>
  apply(method: 'permission.set', params: { mode: ApplicationState['permission_mode'] }): Promise<void>
  /** 检查并加载指令与 MCP 配置 */
  inspect(scope: ConfigScope): Promise<void>
  /** 保存指令或 MCP 配置文件 */
  save(scope: ConfigScope, kind: 'agent' | 'mcp', content: string): Promise<void>
  /** 移除项目级别的指令或 MCP 覆盖项 */
  remove(kind: 'agent' | 'mcp'): Promise<void>
  /** 开启或停用指定技能 */
  toggleSkill(scope: ConfigScope, id: string, enabled: boolean): Promise<void>
  /** 发起 MCP 服务的连通性测试 */
  testMcp(scope: ConfigScope, id: string): Promise<void>
  /** 弹出对话框导入技能目录 */
  importSkills(scope: ConfigScope): Promise<void>
  /** 测试当前配置的大模型连接 */
  testProfile(): Promise<string>
  /** 读取指定技能 Markdown 正文 */
  readSkill(scope: ConfigScope, id: string): Promise<{ content: string; source: string } | null>
  /** 保存对指定技能正文的修改 */
  saveSkill(scope: ConfigScope, id: string, content: string): Promise<void>
}

/**
 * 通用单插件设置组件属性接口。
 */
export type PluginSettingsProps = {
  /** 目标插件配置元数据 */
  plugin: {
    id: string
    enabled: boolean
    loaded?: boolean
    configuration?: {
      router: { model: string; base_url: string }
      runtime: { python: string; node: string }
    }
  }
  /** 系统是否处于忙碌状态 */
  busy: boolean
  /** 提交插件配置更新回调 */
  configure(config: {
    id: string
    enabled: boolean
    router: { model: string; base_url: string; api_key: string }
    runtime: { python: string; node: string }
  }): Promise<void> | void
}

/**
 * 偏好设置组件全局注册表。
 */
export const settingsRegistry: {
  user_config: ComponentType<UserSettingsPageProps>
} = {
  user_config: UserConfig
}
