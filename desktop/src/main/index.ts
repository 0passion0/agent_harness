import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from 'electron'
import { mkdirSync, realpathSync, statSync } from 'node:fs'
import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { AuthStore, ticketProfile } from './auth-store'
import { Backend } from './backend'
import { assertFilePaths, assertSender, validateCommand } from './commands'
import { DesktopApplication } from './application'
import { SsoLogin } from './sso-login'
import { scanDirectory, copyFilesToWorkspace } from './workspace-files'
import type { ConfigScope, EventFrame, UserProfile } from '../shared/protocol'

app.setName('MedPeer')
app.setPath('userData', path.join(app.getPath('appData'), 'agent-harness-desktop'))
const root = path.resolve(import.meta.dirname, '../../..')
const dataDirectory = app.getPath('userData')
const rendererFile = path.join(import.meta.dirname, '../renderer/index.html')
const rendererUrl = new URL(process.env.ELECTRON_RENDERER_URL ?? pathToFileURL(rendererFile).href).href
let window: BrowserWindow
let auth: AuthStore
let login: SsoLogin
let desktop: DesktopApplication
let startup: Promise<unknown>
let restoreError: Error | null = null
const devUser: UserProfile = { userID: -1, realName: '本地开发', userName: 'local-dev', userAvatar: '', userPhone: '', userCredit: 0, isCo: false, isBeta: false }
const skipLogin = !app.isPackaged
const emit = (frame: EventFrame): void => { if (window && !window.isDestroyed()) window.webContents.send('backend:event', frame) }
const backend = new Backend(root, frame => desktop.receive(frame))

function trusted(event: Electron.IpcMainInvokeEvent): void {
  if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw new Error('不受信任的桌面调用')
  assertSender(event.senderFrame.url, rendererUrl)
}
function handle(channel: string, action: (...args: any[]) => Promise<unknown> | unknown): void {
  ipcMain.handle(channel, async (event, ...args) => { trusted(event); await startup; return desktop.serial(async () => action(...args)) })
}
function text(value: unknown): string { if (typeof value !== 'string' || !value.trim()) throw new Error('参数不能为空'); return value }
function scope(value: unknown): ConfigScope { if (value !== 'global' && value !== 'project') throw new Error('配置范围无效'); return value }
function idle(): void { if (desktop.isRunning()) throw new Error('请先结束当前任务') }
function workspacePath(value: unknown): string {
  const base = realpathSync(desktop.workspace()), file = realpathSync(text(value)), relative = path.relative(base, file)
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error('路径不在当前工作区内')
  return file
}
async function open(file: string): Promise<void> { const error = await shell.openPath(file); if (error) throw new Error(error) }

async function start(): Promise<void> {
  Menu.setApplicationMenu(null)
  mkdirSync(path.join(dataDirectory, 'logs'), { recursive: true })
  auth = new AuthStore(path.join(dataDirectory, 'credentials.bin'))
  desktop = new DesktopApplication(dataDirectory, backend, () => skipLogin ? devUser : auth.user, emit)
  startup = (skipLogin ? Promise.resolve() : auth.restore().catch(error => { restoreError = error })).then(() => desktop.initialize())
  window = new BrowserWindow({ width: 1440, height: 900, minWidth: 640, minHeight: 520, show: false, icon: path.join(root, 'assets/medpeer.ico'),
    webPreferences: { preload: path.join(import.meta.dirname, '../preload/index.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false } })
  login = new SsoLogin(() => window, (ticket, details, cookies) => desktop.serial(async () => {
    const profile = ticketProfile(ticket, details)
    idle(); auth.save(profile, cookies); restoreError = null; return desktop.accountChanged()
  }))
  handle('backend:command', raw => { const command = validateCommand(raw); if (restoreError && command.method === 'app.snapshot') throw restoreError; return desktop.command(command) })
  ipcMain.handle('auth:login', async event => { trusted(event); await startup; return login.login() })
  handle('auth:logout', () => { idle(); login.close(); auth.clear(); restoreError = null; return desktop.accountChanged() })
  handle('workspace:select', async () => {
    idle(); const result = await dialog.showOpenDialog(window, { properties: ['openDirectory', 'createDirectory'] })
    return result.canceled ? null : desktop.bindWorkspace(realpathSync(result.filePaths[0]))
  })
  handle('storage:open', async kind => { if (kind !== 'data' && kind !== 'logs') throw new Error('目录类型无效'); await open(kind === 'logs' ? path.join(dataDirectory, 'logs') : dataDirectory) })
  handle('artifact:open', async file => open(desktop.storage().resolveArtifact(text(file))))
  handle('artifact:read', file => desktop.storage().readArtifact(text(file)))
  handle('skill:import', async value => {
    idle(); const target = scope(value), result = await dialog.showOpenDialog(window, { properties: ['openDirectory'] })
    return result.canceled ? null : desktop.storage().importSkill(target, result.filePaths[0])
  })
  handle('attachment:import', async () => {
    const result = await dialog.showOpenDialog(window, { properties: ['openFile', 'multiSelections'] })
    return result.canceled ? [] : desktop.storage().importAttachments(result.filePaths)
  })
  handle('attachment:import-dropped', raw => { assertFilePaths(raw); return desktop.storage().importAttachments(raw) })
  handle('attachment:open', id => open(desktop.storage().resolveAttachment(text(id))))
  handle('attachment:read', id => desktop.storage().readAttachment(text(id)))
  handle('external:open', value => { const url = new URL(text(value)); if (!['https:', 'http:', 'mailto:'].includes(url.protocol)) throw new Error('链接协议不受支持'); return shell.openExternal(url.href) })
  handle('session:export', async id => {
    const result = await desktop.command({ id: 'export', method: 'session.export', params: { session_id: text(id) } }) as { text: string }
    const destination = await dialog.showSaveDialog(window, { defaultPath: 'conversation.md', filters: [{ name: 'Markdown', extensions: ['md'] }] })
    if (destination.canceled || !destination.filePath) return null
    await writeFile(destination.filePath, result.text, 'utf8'); return destination.filePath
  })
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
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', event => event.preventDefault())
  window.once('ready-to-show', () => window.show())
  if (process.env.ELECTRON_RENDERER_URL) await window.loadURL(rendererUrl)
  else await window.loadFile(rendererFile)
}

if (!app.requestSingleInstanceLock()) app.quit()
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus() } })
  let quitting = false
  app.on('before-quit', event => {
    if (quitting) return
    event.preventDefault(); login?.close()
    void backend.close().then(() => { quitting = true; app.quit() }, error => { console.error(error); app.exit(1) })
  })
  app.on('window-all-closed', () => app.quit())
  void app.whenReady().then(start).catch(error => { console.error(error); app.exit(1) })
}
