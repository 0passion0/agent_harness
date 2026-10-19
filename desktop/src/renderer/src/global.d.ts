/**
 * @file global.d.ts
 * @description 渲染进程全局类型声明文件。
 * 将 Preload 脚本中挂载到 window.medpeer 的 DesktopAPI 强类型接口注入至 TypeScript 全局 Window 接口。
 * @author tongliga
 * @date 2026-10-16
 */

import type { DesktopAPI } from '../../shared/protocol'

declare global {
  interface Window {
    /** Electron 主进程通过 Preload 暴露的桌面原生桥接 API */
    medpeer: DesktopAPI
  }
}
