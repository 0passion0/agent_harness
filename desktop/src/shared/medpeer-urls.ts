/** 登录端点只在主进程从非敏感部署配置读取。 */
import { readFileSync } from 'node:fs'
import path from 'node:path'

type Services = {
  sso_user_origin: string; sso_project_origin: string; sso_login_path: string
  sso_callback_path: string; sso_callback_project_id: string
  sso_pt_cookie_path: string; sso_user_information_path: string
}
const configPath = process.env.MEDPEER_CONFIG_FILE || path.resolve(import.meta.dirname, '../../settings.example.json')
const { services } = JSON.parse(readFileSync(configPath, 'utf8')) as { services: Services }

/** 校验外部部署配置的必填 URL origin。 */
function origin(value: string): string {
  const url = new URL(value)
  if (!['http:', 'https:'].includes(url.protocol) || url.href !== `${url.origin}/`) throw new Error('SSO origin 无效')
  return url.origin
}
/** 将显式的服务路径组合成 URL，拒绝跨源路径。 */
function endpoint(base: string, value: string): string {
  if (!value.startsWith('/') || value.startsWith('//')) throw new Error('SSO path 无效')
  const url = new URL(value, base)
  if (url.origin !== base) throw new Error('SSO path 跨源')
  return url.href
}
const userOrigin = origin(process.env.MEDPEER_SSO_USER_ORIGIN || services.sso_user_origin)
const projectOrigin = origin(process.env.MEDPEER_SSO_PROJECT_ORIGIN || services.sso_project_origin)
if (!services.sso_callback_project_id) throw new Error('缺少 SSO 项目 ID')
const callback = new URL(endpoint(projectOrigin, services.sso_callback_path))
callback.searchParams.set('id', services.sso_callback_project_id)
export const medpeerUrls = {
  login: endpoint(userOrigin, services.sso_login_path),
  callback: `${callback.href}&`,
  ptCookie: endpoint(projectOrigin, services.sso_pt_cookie_path),
  userInformation: endpoint(userOrigin, services.sso_user_information_path)
}
