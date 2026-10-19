import { safeStorage } from 'electron'
import { createHmac, timingSafeEqual } from 'node:crypto'
import { existsSync, readFileSync, unlinkSync } from 'node:fs'
import type { Json, UserProfile } from '../shared/protocol'
import { medpeerUrls } from '../shared/medpeer-urls'
import { writeAtomic } from './local-store'
import { validateUserInformation } from './sso-validation'

type Details = ReturnType<typeof validateUserInformation>
type Credentials = { cookies: string; profile: UserProfile }

// This is the upstream ticket format; identity is additionally checked by the SSO service.
export function ticketProfile(ticket: string, details: Details): UserProfile {
  if (!ticket || ticket.length > 16384) throw new Error('登录凭据格式无效')
  const parts = ticket.replaceAll('_', '/').split('.')
  if (parts.length !== 3 || parts.some(part => !part)) throw new Error('登录凭据格式无效')
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(parts[1])) throw new Error('登录凭据格式无效')
  const payload = JSON.parse(Buffer.from(parts[1], 'base64').toString('utf8'))
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('登录凭据格式无效')
  if (!((typeof payload.userID === 'string' && payload.userID.length > 0) || (typeof payload.userID === 'number' && Number.isInteger(payload.userID)))) throw new Error('登录用户无效')
  if (typeof payload.exp !== 'number' || !Number.isFinite(payload.exp) || payload.exp <= Date.now() / 1000) throw new Error('登录凭据已过期')
  const signature = Buffer.from(createHmac('sha256', `MedPeer_${payload.userID}`).update(parts[0] + parts[1]).digest('hex')).toString('base64')
  if (signature.length !== parts[2].length || !timingSafeEqual(Buffer.from(signature), Buffer.from(parts[2]))) throw new Error('登录凭据校验失败')
  if (String(payload.userID) !== String(details.user_id)) throw new Error('登录账号不一致')
  for (const key of ['realName', 'userName', 'userAvatar']) if (typeof payload[key] !== 'string') throw new Error('用户资料字段无效')
  if (typeof payload.token !== 'string' || !payload.token || !('isCo' in payload) || !('isBeta' in payload)) throw new Error('用户资料字段不完整')
  return { userID: details.user_id, realName: payload.realName, userName: payload.userName,
    userAvatar: payload.userAvatar, userPhone: details.user_phone, userCredit: details.user_credit,
    isCo: payload.isCo as Json, isBeta: payload.isBeta as Json }
}

export class AuthStore {
  private credentials: Credentials | null = null
  constructor(private file: string) {}
  get user(): UserProfile | null { return this.credentials?.profile ?? null }

  save(profile: UserProfile, cookies: string): void {
    if (!safeStorage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用')
    const credentials = { cookies, profile }
    writeAtomic(this.file, safeStorage.encryptString(JSON.stringify(credentials)))
    this.credentials = credentials
  }

  async restore(): Promise<void> {
    if (!existsSync(this.file)) return
    if (!safeStorage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用')
    const saved = JSON.parse(safeStorage.decryptString(readFileSync(this.file))) as Credentials
    const response = await fetch(medpeerUrls.userInformation, { method: 'POST', headers: { Cookie: saved.cookies }, redirect: 'error', signal: AbortSignal.timeout(15000) })
    if (response.status === 401 || response.status === 403) { this.clear(); return }
    if (!response.ok) throw new Error(`登录状态验证失败 (${response.status})`)
    const raw = await response.json()
    if (raw.code === 300) { this.clear(); return }
    const details = validateUserInformation(raw)
    if (details.user_id !== saved.profile.userID) throw new Error('登录账号不一致')
    this.credentials = { cookies: saved.cookies, profile: { ...saved.profile, userPhone: details.user_phone, userCredit: details.user_credit } }
  }

  clear(): void {
    if (existsSync(this.file)) unlinkSync(this.file)
    this.credentials = null
  }
}
