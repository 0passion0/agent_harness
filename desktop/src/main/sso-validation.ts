/**
 * @file sso-validation.ts
 * @description SSO 登录回调判定与用户信息契约校验纯函数模块。
 * 不依赖 Electron 任何窗口句柄，可独立进行单元测试。
 * 遵循 Fail-Fast（有错必爆）原则，对外部服务返回的数据进行严格类型与字段边界断言。
 * @author tongliga
 * @date 2026-10-16
 */

import { medpeerUrls } from '../shared/medpeer-urls'

/**
 * 用户信息接口原始返回结构契约。
 */
type UserInformation = {
  result?: boolean
  code?: number
  data?: {
    user_id?: unknown
    user_phone?: unknown
    user_credit?: unknown
  }
}

/**
 * 校验给定的目标 URL 是否属于预期的 MedPeer SSO 回调地址。
 * 禁止仅凭域名相同就误认任意跳转为登录成功，必须同时比对协议、origin、pathname 与查询参数 id。
 *
 * @param candidate - 待验证的完整 URL 字符串
 * @returns 是否为合法的 SSO 认证回调 URL
 */
export function isSsoCallback(candidate: string): boolean {
  try {
    const url = new URL(candidate)
    const expected = new URL(medpeerUrls.callback)
    return (
      url.origin === expected.origin &&
      url.pathname === expected.pathname &&
      url.searchParams.get('id') === expected.searchParams.get('id')
    )
  } catch {
    return false
  }
}

/**
 * 强校验登录服务返回的用户资料载荷，提取必填用户字段。
 *
 * 第一步：校验外层响应格式（result 必须为 true，code 必须为 200）。
 * 第二步：断言核心 data 字段存在且类型为对象。
 * 第三步：校验 user_id 必须为整数，user_phone 为字符串，user_credit 为有限数值。
 *
 * @param result - 服务端返回的原始 JSON 解码对象
 * @returns 提取出的结构化有效用户数据对象
 * @throws Error 当返回数据结构缺失关键字段或类型不匹配时立即抛出
 */
export function validateUserInformation(result: unknown): {
  user_id: number
  user_phone: string
  user_credit: number
} {
  const response = result as UserInformation | null
  const information = response?.data

  // 严格 Fail-Fast 校验，禁止空假默认值静默回退
  if (
    response?.result !== true ||
    response.code !== 200 ||
    !information ||
    typeof information.user_id !== 'number' ||
    !Number.isInteger(information.user_id) ||
    typeof information.user_phone !== 'string' ||
    typeof information.user_credit !== 'number' ||
    !Number.isFinite(information.user_credit)
  ) {
    throw new Error('登录服务返回的用户资料无效')
  }

  return {
    user_id: information.user_id,
    user_phone: information.user_phone,
    user_credit: information.user_credit
  }
}
