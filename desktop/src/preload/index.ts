/**
 * @file index.ts
 * @description Electron Preload 预加载脚本。
 * 通过 contextBridge 将受控的 DesktopAPI 安全能力暴露注入到渲染进程 window.medpeer 对象中。
 * 遵循沙箱隔离准则，严禁向渲染进程直接透传原始 Node.js 模块或未受控的 IPC 句柄。
 * @author tongliga
 * @date 2026-10-16
 */

import { contextBridge, ipcRenderer, webUtils } from 'electron'
import type { Command, DesktopAPI, EventFrame } from '../shared/protocol'

/**
 * 暴露给渲染进程的安全 API 实现对象。
 */
const api: DesktopAPI = {
  /** 打开系统文件管理器并定位到数据或日志目录 */
  openDataDirectory: (kind) => ipcRenderer.invoke('storage:open', kind),

  /** 弹出系统原生文件夹选择对话框以绑定工作区目录 */
  selectWorkspace: () => ipcRenderer.invoke('workspace:select'),

  /** 向 Python 后端发送标准 JSON-RPC 命令 */
  command: (command: Command) => ipcRenderer.invoke('backend:command', command),

  /** 调起 MedPeer SSO 登录模态窗口 */
  login: () => ipcRenderer.invoke('auth:login'),

  /** 注销当前用户登录状态并清除本地凭据 */
  logout: () => ipcRenderer.invoke('auth:logout'),

  /** 使用系统默认程序打开生成的交付物成果文件 */
  openArtifact: (path: string) => ipcRenderer.invoke('artifact:open', path),

  /** 弹出目录选择框并导入技能目录 */
  importSkill: (scope) => ipcRenderer.invoke('skill:import', scope),

  /** 弹出系统文件选择框导入对话附件 */
  importAttachments: () => ipcRenderer.invoke('attachment:import'),

  /** 导入从外部拖拽放入的本地文件列表为对话附件（使用 webUtils 安全提取本机物理路径） */
  importDroppedFiles: (files) =>
    ipcRenderer.invoke('attachment:import-dropped', files.map(file => webUtils.getPathForFile(file))),

  /** 使用系统关联程序打开指定的附件文件 */
  openAttachment: (id: string) => ipcRenderer.invoke('attachment:open', id),

  /** 读取指定附件的原始 base64 内容与媒体类型 */
  readAttachment: (id: string) => ipcRenderer.invoke('attachment:read', id),

  /** 读取指定交付成果文件的原始 base64 内容与媒体类型 */
  readArtifact: (path: string) => ipcRenderer.invoke('artifact:read', path),

  /** 使用系统默认浏览器打开外部网页链接 */
  openExternal(url: string) {
    return ipcRenderer.invoke('external:open', url)
  },

  /** 导出指定会话的历史记录为本地文本文件 */
  exportSession: (sessionId: string) => ipcRenderer.invoke('session:export', sessionId),

  /** 递归扫描工作区目录下的文件与文件夹树结构 */
  listWorkspaceDirectory: (dirPath?: string) => ipcRenderer.invoke('workspace:list-files', dirPath),

  /** 使用系统默认应用直接打开工作区文件 */
  openWorkspaceFile: (filePath: string) => ipcRenderer.invoke('workspace:open-file', filePath),

  /** 在系统资源管理器中定位并高亮指定文件 */
  showItemInFolder: (filePath: string) => ipcRenderer.invoke('workspace:show-in-folder', filePath),

  /** 在系统资源管理器中打开指定的工作区文件夹 */
  openWorkspaceFolder: (folderPath?: string) => ipcRenderer.invoke('workspace:open-folder', folderPath),

  /** 发起工作区文件的原生 OS 文件拖拽传输 */
  dragWorkspaceFile: (filePath: string) => ipcRenderer.invoke('workspace:drag-file', filePath),

  /** 将外部拖拽的文件复制到指定工作区目标目录下 */
  copyDroppedFilesToWorkspace: (files, targetDir) => {
    const paths = files.map(file => typeof file === 'string' ? file : webUtils.getPathForFile(file))
    return ipcRenderer.invoke('workspace:copy-dropped', paths, targetDir)
  },

  /**
   * 监听后端主动推送的通知事件帧。
   *
   * @param listener - 事件消费函数
   * @returns 取消监听的回调函数
   */
  onEvent(listener: (event: EventFrame) => void) {
    const handler = (_event: Electron.IpcRendererEvent, value: EventFrame) => listener(value)
    ipcRenderer.on('backend:event', handler)
    return () => ipcRenderer.removeListener('backend:event', handler)
  }
}

// 将 API 安全注入至渲染环境的 window.medpeer 命名空间中
contextBridge.exposeInMainWorld('medpeer', api)
