/**
 * @file auth-store.ts
 * @description 用户登录状态与持久化凭据管理模块。
 * 负责使用 Electron safeStorage 加密/解密本地保存的会话 Cookie 与用户资料，
 * 并提供登录票据（Ticket）验签解析与后台服务端凭据校验能力。
 * @author tongliga
 * @date 2026-10-16
 */

import { safeStorage } from 'electron'
import { createHmac, timingSafeEqual } from 'node:crypto'
import { existsSync, readFileSync, unlinkSync } from 'node:fs'
import type { Json, UserProfile } from '../shared/protocol'
import { medpeerUrls } from '../shared/medpeer-urls'
import { writeAtomic } from './local-store'
import { validateUserInformation } from './sso-validation'

/**
 * SSO 用户信息接口校验提取后的用户明细类型。
 */
type Details = ReturnType<typeof validateUserInformation>

/**
 * 本地持久化保存的加密凭据结构。
 */
type Credentials = {
  /** 经过 SSO 认证的会话 Cookie 字符串 */
  cookies: string
  /** 用户基础个人资料 */
  profile: UserProfile
}

/**
 * 解析并校验 SSO 服务签发的上游登录票据（Ticket）载荷。
 *
 * 第一步：校验票据长度与三段式结构合法性。
 * 第二步：Base64 解码载荷并检查必填字段及有效期限。
 * 第三步：使用 MedPeer 密钥与 HMAC-SHA256 算法校验签名一致性。
 * 第四步：核对 Ticket 中的用户 ID 与服务端明细是否一致，组装结构化资料。
 *
 * @param ticket - SSO 服务返回的原始登录票据字符串
 * @param details - 从服务端接口提取验证后的用户身份明细
 * @returns 组装验证后的用户档案对象
 * @throws Error 当票据格式非法、过期、验签失败或账号不一致时抛出
 */
export function ticketProfile(ticket: string, details: Details): UserProfile {
  // 第一步：校验票据容器长度与三段式点分结构
  if (!ticket || ticket.length > 16384) throw new Error('登录凭据格式无效')
  const parts = ticket.replaceAll('_', '/').split('.')
  if (parts.length !== 3 || parts.some(part => !part)) throw new Error('登录凭据格式无效')
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(parts[1])) throw new Error('登录凭据格式无效')

  // 第二步：解码载荷并校验必填字段与有效期
  const payload = JSON.parse(Buffer.from(parts[1], 'base64').toString('utf8'))
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('登录凭据格式无效')
  if (!((typeof payload.userID === 'string' && payload.userID.length > 0) || (typeof payload.userID === 'number' && Number.isInteger(payload.userID)))) throw new Error('登录用户无效')
  if (typeof payload.exp !== 'number' || !Number.isFinite(payload.exp) || payload.exp <= Date.now() / 1000) throw new Error('登录凭据已过期')

  // 第三步：HMAC-SHA256 签名比对防篡改校验
  const signature = Buffer.from(createHmac('sha256', `MedPeer_${payload.userID}`).update(parts[0] + parts[1]).digest('hex')).toString('base64')
  if (signature.length !== parts[2].length || !timingSafeEqual(Buffer.from(signature), Buffer.from(parts[2]))) throw new Error('登录凭据校验失败')

  // 第四步：核对用户身份一致性与字段完整性
  if (String(payload.userID) !== String(details.user_id)) throw new Error('登录账号不一致')
  for (const key of ['realName', 'userName', 'userAvatar']) if (typeof payload[key] !== 'string') throw new Error('用户资料字段无效')
  if (typeof payload.token !== 'string' || !payload.token || !('isCo' in payload) || !('isBeta' in payload)) throw new Error('用户资料字段不完整')

  return {
    userID: details.user_id,
    realName: payload.realName,
    userName: payload.userName,
    userAvatar: payload.userAvatar,
    userPhone: details.user_phone,
    userCredit: details.user_credit,
    isCo: payload.isCo as Json,
    isBeta: payload.isBeta as Json
  }
}

/**
 * 用户凭据安全存储与自动恢复管理器。
 */
export class AuthStore {
  /** 内存中缓存的已解密凭据信息 */
  private credentials: Credentials | null = null

  /**
   * 初始化凭据管理器。
   *
   * @param file - 本地加密凭据保存的绝对物理路径
   */
  constructor(private file: string) {}

  /**
   * 获取当前已登录的用户资料，未登录时返回 null。
   */
  get user(): UserProfile | null {
    return this.credentials?.profile ?? null
  }

  /**
   * 使用系统原生加密层持久化保存当前登录凭据。
   *
   * @param profile - 用户档案资料
   * @param cookies - 认证 Cookie 字符串
   * @throws Error 当操作系统加密模块不可用时抛出
   */
  save(profile: UserProfile, cookies: string): void {
    if (!safeStorage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用')
    const credentials = { cookies, profile }
    writeAtomic(this.file, safeStorage.encryptString(JSON.stringify(credentials)))
    this.credentials = credentials
  }

  /**
   * 从本地加密文件中读取并向服务端验证恢复登录态。
   * 若凭据失效或过期则自动清除本地凭据。
   *
   * @throws Error 当系统凭据加密不可用或网络异常时抛出
   */
  async restore(): Promise<void> {
    if (!existsSync(this.file)) return
    if (!safeStorage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用')
    const saved = JSON.parse(safeStorage.decryptString(readFileSync(this.file))) as Credentials
    const response = await fetch(medpeerUrls.userInformation, {
      method: 'POST',
      headers: { Cookie: saved.cookies },
      redirect: 'error',
      signal: AbortSignal.timeout(15000)
    })
    if (response.status === 401 || response.status === 403) {
      this.clear()
      return
    }
    if (!response.ok) throw new Error(`登录状态验证失败 (${response.status})`)
    const raw = await response.json()
    if (raw.code === 300) {
      this.clear()
      return
    }
    const details = validateUserInformation(raw)
    if (details.user_id !== saved.profile.userID) throw new Error('登录账号不一致')
    this.credentials = {
      cookies: saved.cookies,
      profile: { ...saved.profile, userPhone: details.user_phone, userCredit: details.user_credit }
    }
  }

  /**
   * 清除本地持久化凭据文件与内存缓存。
   */
  clear(): void {
    if (existsSync(this.file)) unlinkSync(this.file)
    this.credentials = null
  }
}

