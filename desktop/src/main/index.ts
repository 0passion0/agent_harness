/**
 * @file index.ts
 * @description Electron 主进程主入口文件。
 * 负责应用程序生命周期管理、主窗口创建、原生上下文安全配置、
 * IPC 通道路由注册（命令调度、工作区选择、凭据管理、文件导入与拖拽）以及优雅退出机制。
 * @author tongliga
 * @date 2026-10-16
 */

import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from 'electron'
import { mkdirSync, realpathSync, statSync } from 'node:fs'
import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { Backend } from './backend'
import { assertFilePaths, assertSender, validateCommand } from './commands'
import { DesktopApplication } from './application'
import { scanDirectory, copyFilesToWorkspace } from './workspace-files'
import type { EventFrame } from '../shared/protocol'

app.setName('MedPeer')
app.setPath('userData', path.join(app.getPath('appData'), 'agent-harness-desktop'))

const root = path.resolve(import.meta.dirname, '../../..')
const dataDirectory = app.getPath('userData')
const rendererFile = path.join(import.meta.dirname, '../renderer/index.html')
const rendererUrl = new URL(process.env.ELECTRON_RENDERER_URL ?? pathToFileURL(rendererFile).href).href

let window: BrowserWindow
let desktop: DesktopApplication
let startup: Promise<unknown>


/** 向渲染进程广播后端事件帧 */
const emit = (frame: EventFrame): void => {
  if (window && !window.isDestroyed()) window.webContents.send('backend:event', frame)
}
const backend = new Backend(root, frame => desktop.receive(frame), app.isPackaged)

/**
 * 校验 IPC 调用的来源安全性。
 * 确保调用必须由当前主窗口的顶层主 Frame 发起，且 URL 与受信任页面一致。
 *
 * @param event - Electron IPC 事件对象
 * @throws Error 当调用者不受信任时抛出
 */
function trusted(event: Electron.IpcMainInvokeEvent): void {
  if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw new Error('不受信任的桌面调用')
  assertSender(event.senderFrame.url, rendererUrl)
}

/**
 * 注册经过来源校验与队列串行化包装的 IPC 处理函数。
 *
 * @param channel - IPC 监听频道名
 * @param action - 执行业务逻辑函数
 */
function handle(channel: string, action: (...args: any[]) => Promise<unknown> | unknown): void {
  ipcMain.handle(channel, async (event, ...args) => {
    trusted(event)
    await startup
    return desktop.serial(async () => action(...args))
  })
}

/** 校验并断言传入值为非空字符串 */
function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('参数不能为空')
  return value
}



/** 断言当前无运行中任务 */
function idle(): void {
  if (desktop.isRunning()) throw new Error('请先结束当前任务')
}

/**
 * 校验并解析工作区内的本地文件绝对物理路径，严禁路径逃逸。
 *
 * @param value - 待解析路径参数
 * @returns 规范化的绝对物理路径
 * @throws Error 当文件逃出工作区或参数非法时抛出
 */
function workspacePath(value: unknown): string {
  const base = realpathSync(desktop.workspace()), file = realpathSync(text(value)), relative = path.relative(base, file)
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error('路径不在当前工作区内')
  return file
}

/** 调用系统原生默认程序打开指定文件或路径 */
async function open(file: string): Promise<void> {
  const error = await shell.openPath(file)
  if (error) throw new Error(error)
}

/**
 * 应用程序就绪并启动主窗口初始化流程。
 */
async function start(): Promise<void> {
  Menu.setApplicationMenu(null)
  mkdirSync(path.join(dataDirectory, 'logs'), { recursive: true })
  desktop = new DesktopApplication(dataDirectory, backend, emit)
  startup = desktop.initialize()

  window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 640,
    minHeight: 520,
    show: false,
    icon: path.join(root, 'assets/medpeer.ico'),
    webPreferences: {
      preload: path.join(import.meta.dirname, '../preload/index.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  })

  // ===== 1. 后端命令 IPC 路由 =====
  handle('backend:command', raw => {
    const command = validateCommand(raw)
    return desktop.command(command)
  })

  // ===== 2. 工作区绑定与数据目录路由 =====
  handle('workspace:select', async () => {
    idle()
    const result = await dialog.showOpenDialog(window, { properties: ['openDirectory', 'createDirectory'] })
    return result.canceled ? null : desktop.bindWorkspace(realpathSync(result.filePaths[0]))
  })
  handle('storage:open', async kind => {
    if (kind !== 'data' && kind !== 'logs') throw new Error('目录类型无效')
    await open(kind === 'logs' ? path.join(dataDirectory, 'logs') : dataDirectory)
  })

  // ===== 3. 交付成果与技能导入路由 =====
  handle('artifact:open', async file => open(desktop.storage().resolveArtifact(text(file))))
  handle('artifact:read', file => desktop.storage().readArtifact(text(file)))

  // ===== 4. 附件导入、拖拽与读取路由 =====
  handle('attachment:import', async () => {
    const result = await dialog.showOpenDialog(window, { properties: ['openFile', 'multiSelections'] })
    return result.canceled ? [] : desktop.storage().importAttachments(result.filePaths)
  })
  handle('attachment:import-dropped', raw => {
    assertFilePaths(raw)
    return desktop.storage().importAttachments(raw)
  })
  handle('attachment:open', id => open(desktop.storage().resolveAttachment(text(id))))
  handle('attachment:read', id => desktop.storage().readAttachment(text(id)))

  // ===== 5. 外部链接与会话导出路由 =====
  handle('external:open', value => {
    const url = new URL(text(value))
    if (!['https:', 'http:', 'mailto:'].includes(url.protocol)) throw new Error('链接协议不受支持')
    return shell.openExternal(url.href)
  })
  handle('session:export', async id => {
    const result = await desktop.command({ id: 'export', method: 'session.export', params: { session_id: text(id) } }) as { text: string }
    const destination = await dialog.showSaveDialog(window, { defaultPath: 'conversation.md', filters: [{ name: 'Markdown', extensions: ['md'] }] })
    if (destination.canceled || !destination.filePath) return null
    await writeFile(destination.filePath, result.text, 'utf8')
    return destination.filePath
  })

  // ===== 6. 工作区文件浏览、定位与原生拖拽 =====
  handle('workspace:list-files', value => value ? scanDirectory(workspacePath(value)) : [])
  handle('workspace:open-file', file => open(workspacePath(file)))
  handle('workspace:show-in-folder', file => shell.showItemInFolder(workspacePath(file)))
  handle('workspace:open-folder', file => open(file === undefined ? desktop.workspace() : workspacePath(file)))
  handle('workspace:drag-file', file => window.webContents.startDrag({ file: workspacePath(file), icon: path.join(root, 'assets/medpeer.ico') }))
  handle('workspace:copy-dropped', (raw, target) => {
    assertFilePaths(raw)
    for (const file of raw) if (!statSync(file).isFile()) throw new Error('仅支持文件')
    return copyFilesToWorkspace(raw, workspacePath(target))
  })

  // 窗口安全限制：禁止打开非受控新窗口与非预期导航
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', event => event.preventDefault())
  window.once('ready-to-show', () => window.show())

  if (process.env.ELECTRON_RENDERER_URL) await window.loadURL(rendererUrl)
  else await window.loadFile(rendererFile)
}

// 保证单实例运行
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (window) {
      if (window.isMinimized()) window.restore()
      window.focus()
    }
  })

  let quitting = false
  app.on('before-quit', event => {
    if (quitting) return
    event.preventDefault()
    void backend.close().then(
      () => {
        quitting = true
        app.quit()
      },
      error => {
        console.error(error)
        app.exit(1)
      }
    )
  })

  app.on('window-all-closed', () => app.quit())
  void app.whenReady().then(start).catch(error => {
    console.error(error)
    app.exit(1)
  })
}

