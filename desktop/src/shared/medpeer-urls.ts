/**
 * @file medpeer-urls.ts
 * @description MedPeer 单点登录（SSO）服务端接口端点配置与 URL 组装模块。
 * 从部署配置文件或环境变量中动态解析 SSO 认证中心、回调地址、凭据 Cookie 及用户信息端点，
 * 并严格校验 URL Origin 与路径防止跨源泄露。
 * @author tongliga
 * @date 2026-10-16
 */

import { readFileSync } from 'node:fs'
import path from 'node:path'

/**
 * SSO 服务部署与路由配置契约。
 */
type Services = {
  /** SSO 用户认证服务基地址 Origin */
  sso_user_origin: string
  /** SSO 项目/业务中心基地址 Origin */
  sso_project_origin: string
  /** 登录页面相对路径 */
  sso_login_path: string
  /** 登录成功重定向回调页面相对路径 */
  sso_callback_path: string
  /** SSO 登录对接分配的项目唯一标识 ID */
  sso_callback_project_id: string
  /** 获取 pt 凭据 Cookie 的接口相对路径 */
  sso_pt_cookie_path: string
  /** 查询用户身份详情信息的接口相对路径 */
  sso_user_information_path: string
}

// 优先读取环境变量指定的配置文件路径，回退至 settings.example.json 默认配置
const configPath = process.env.MEDPEER_CONFIG_FILE || path.resolve(import.meta.dirname, '../../settings.example.json')
const { services } = JSON.parse(readFileSync(configPath, 'utf8')) as { services: Services }

/**
 * 校验外部部署配置中的 URL Origin 合法性。
 * 限制仅支持 http/https 协议，且结尾必须为根路径 `/`。
 *
 * @param value - 待校验的原始 Origin 字符串
 * @returns 规范化的 Origin 字符串
 * @throws Error 当协议非法或存在多余路径时抛出
 */
function origin(value: string): string {
  const url = new URL(value)
  if (!['http:', 'https:'].includes(url.protocol) || url.href !== `${url.origin}/`) throw new Error('SSO origin 无效')
  return url.origin
}

/**
 * 将相对服务端路径与基准 Origin 安全拼装为完整 URL。
 * 严禁绝对协议相对路径（以 // 开头）以防止跨源 SSRF 攻击。
 *
 * @param base - 基准 Origin 域名
 * @param value - 服务相对路由路径（必须以 `/` 开头）
 * @returns 拼装后的完整绝对 URL 字符串
 * @throws Error 当路径格式非法或目标 Origin 与基准不一致时抛出
 */
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

/**
 * 预解析并经过安全校验的 MedPeer SSO 核心服务端点集合。
 */
export const medpeerUrls = {
  /** 唤起登录的主界面 URL 地址 */
  login: endpoint(userOrigin, services.sso_login_path),
  /** 认证成功后拦截的预期回调地址特征字符串 */
  callback: `${callback.href}&`,
  /** 认证换取持久化 pt 票据 Cookie 的端点 URL */
  ptCookie: endpoint(projectOrigin, services.sso_pt_cookie_path),
  /** 查询当前登录用户详细资料（姓名、手机号、研值）的端点 URL */
  userInformation: endpoint(userOrigin, services.sso_user_information_path)
}

