/**
 * @file sso-login.ts
 * @description MedPeer 单点登录（SSO）模态认证窗口控制器。
 * 仅在 Electron 主进程运行，负责创建隔离的登录窗口、监听认证重定向、
 * 安全提取登录票据 pt 及会话 Cookie，并在校验成功后持久化用户资料。
 * 敏感票据与 Cookie 绝不传递给渲染进程。
 * @author tongliga
 * @date 2026-10-16
 */

import { BrowserWindow, shell } from 'electron'
import { randomUUID } from 'node:crypto'
import type { ApplicationState } from '../shared/protocol'
import { medpeerUrls } from '../shared/medpeer-urls'
import { isSsoCallback, validateUserInformation } from './sso-validation'

/**
 * 用户持久化保存委托函数签名。
 */
type SaveUser = (
  pt: string,
  details: { user_id: number; user_phone: string; user_credit: number },
  userCookies: string
) => Promise<ApplicationState>

/**
 * SSO 单点登录窗口生命周期管理器。
 */
export class SsoLogin {
  /** 当前打开的登录弹窗实例 */
  private dialog: BrowserWindow | null = null
  /** 是否存在正在进行中的登录事务 */
  private pending = false

  /**
   * 初始化 SSO 登录管理器。
   *
   * @param parent - 获取父级主窗口的函数
   * @param saveUser - 用户登录成功后的持久化保存回调
   */
  constructor(
    private parent: () => BrowserWindow,
    private saveUser: SaveUser
  ) {}

  /**
   * 调起单点登录弹窗并等待用户完成认证流程。
   *
   * @returns 认证成功后的最新应用程序状态
   * @throws Error 当已有登录窗口处于打开状态或登录被取消/失败时抛出
   */
  async login(): Promise<ApplicationState> {
    if (this.pending) throw new Error('登录窗口已打开，请完成或关闭当前登录')
    this.pending = true
    try {
      return await this.open()
    } finally {
      this.pending = false
    }
  }

  /**
   * 强制关闭并销毁当前的登录弹窗。
   */
  close(): void {
    const dialog = this.dialog
    this.dialog = null
    if (dialog && !dialog.isDestroyed()) dialog.close()
  }

  /**
   * 创建独立沙箱隔离的登录窗口并监听 OAuth/SSO 回调。
   *
   * 第一步：使用随机 UUID partition 隔离会话存储，防止残留 Cookie 污染业务窗口。
   * 第二步：拼装携带回调地址的 SSO 登录 URL 并加载。
   * 第三步：监听 did-navigate 页面导航，匹配合法回调端点。
   * 第四步：从 URL 参数或会话 Cookie 中提取关键登录票据 pt。
   * 第五步：使用凭证在主进程安全请求用户信息接口，并进行纯函数强契约校验。
   * 第六步：调用 saveUser 回调将持久化结果存入本地数据库，结束流程。
   */
  private open(): Promise<ApplicationState> {
    return new Promise((resolve, reject) => {
      let completed = false
      let processing = false
      let callbackTicket: string | null = null

      // 收尾清理与结果兑现辅助函数
      const finish = (error?: unknown, state?: ApplicationState) => {
        if (completed) return
        completed = true
        this.close()
        if (error) reject(error)
        else if (state) resolve(state)
        else reject(new Error('登录未完成'))
      }

      // 第一步：构建 SSO 登录目标 URL，设置回调地址
      const target = new URL(medpeerUrls.login)
      target.searchParams.set('url', medpeerUrls.callback)

      // 第二步：创建模态弹窗，使用临时随机 partition 严格隔离 Cookie
      const dialog = new BrowserWindow({
        parent: this.parent(),
        modal: true,
        width: 760,
        height: 840,
        minWidth: 440,
        minHeight: 520,
        autoHideMenuBar: true,
        show: false,
        webPreferences: {
          contextIsolation: true,
          sandbox: true,
          nodeIntegration: false,
          partition: `medpeer-login-${randomUUID()}`
        }
      })
      this.dialog = dialog

      dialog.once('ready-to-show', () => { if (!dialog.isDestroyed()) dialog.show() })
      dialog.on('closed', () => finish(new Error('登录已取消')))

      // 第三步：拦截非标准协议跳转
      dialog.webContents.on('will-navigate', (event, destination) => {
        try {
          if (!['http:', 'https:'].includes(new URL(destination).protocol)) event.preventDefault()
          if (isSsoCallback(destination)) callbackTicket = new URL(destination).searchParams.get('pt')
        } catch { event.preventDefault() }
      })

      // 第四步：监听导航落地，捕获 SSO 回调并提取凭证
      dialog.webContents.on('did-navigate', (_event, destination) => {
        if (processing || completed || !isSsoCallback(destination)) return
        processing = true
        void (async () => {
          // 4.1 提取 pt 票据，保留服务端重定向前回调 URL 中的值
          const url = new URL(destination)
          const cookies = await dialog.webContents.session.cookies.get({ url: medpeerUrls.ptCookie })
          const pt = url.searchParams.get('pt') || callbackTicket || cookies.find(cookie => cookie.name === 'pt')?.value
          if (!pt) throw new Error('登录回调缺少凭据，请重新登录')

          // 4.2 请求用户中心获取用户基础资料
          const response = await dialog.webContents.session.fetch(medpeerUrls.userInformation, {
            method: 'POST',
            credentials: 'include'
          })
          if (!response.ok) throw new Error('无法获取用户信息，请检查网络或重新登录')

          // 4.3 校验数据结构有效性
          const data = validateUserInformation(await response.json())
          const userCookies = await dialog.webContents.session.cookies.get({ url: medpeerUrls.userInformation })
          if (!userCookies.length) throw new Error('登录回调缺少会话 Cookie，请重新登录')

          // 4.4 调用持久化回调保存
          return this.saveUser(pt, data, userCookies.map(cookie => `${cookie.name}=${cookie.value}`).join('; '))
        })().then(state => finish(undefined, state), error => finish(error))
      })

      // 第五步：处理弹窗内外部链接跳转（在外部浏览器安全打开）
      dialog.webContents.setWindowOpenHandler(({ url }) => {
        try {
          const destination = new URL(url)
          if (destination.protocol === 'https:' && destination.hostname === new URL(medpeerUrls.login).hostname)
            void shell.openExternal(destination.href)
        } catch { /* 拒绝非法网址 */ }
        return { action: 'deny' }
      })

      // 加载登录页面
      void dialog.loadURL(target.href).catch(error => finish(error))
    })
  }
}
