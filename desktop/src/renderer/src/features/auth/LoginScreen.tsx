/**
 * @file LoginScreen.tsx
 * @description MedPeer 独立单点登录引导屏组件。
 * 负责在应用尚未检测到有效登录态时呈现整屏登录引导，说明本地优先的数据安全准则，
 * 并提供唤起主进程 SSO 登录模态窗的入口操作。
 * @author tongliga
 * @date 2026-10-16
 */

import { useState } from 'react'
import { ArrowRight, LockKeyhole } from 'lucide-react'
import './auth.css'

/**
 * 登录屏幕组件属性接口。
 */
type LoginScreenProps = {
  /** 界面深浅色主题外观 */
  theme: 'light' | 'dark'
}

/**
 * 独立单点登录引导视图组件。
 *
 * 第一步：维护登录等待状态 (pending) 与异常错误提示 (error)。
 * 第二步：点击登录按钮调用 window.medpeer.login()，在主进程打开独立 SSO 隔离弹窗。
 * 第三步：若发生网络异常或用户取消登录，呈现友好告警信息。
 *
 * @param props - 组件属性
 */
export function LoginScreen({ theme }: LoginScreenProps) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  /** 触发主进程单点登录流程 */
  async function login() {
    if (pending) return
    setPending(true)
    setError('')
    try {
      await window.medpeer.login()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setPending(false)
    }
  }

  return <div className={`app product-v2 product-v3 ${theme}`}>
    <main className="auth-page">
      <section className="auth-panel" aria-labelledby="login-title">
        <div className="auth-logo">
          <LockKeyhole size={25} aria-hidden="true" />
        </div>
        <p className="auth-eyebrow">MEDPEER WORKSPACE</p>
        <h1 id="login-title">登录 MedPeer</h1>
        <p className="auth-description">
          登录后进入科研工作台。账号信息将保存在本机，敏感凭据由系统凭据管理器保管。
        </p>
        <button className="primary-action auth-submit" disabled={pending} onClick={() => void login()}>
          {pending ? '正在验证账号…' : '前往账号登录'} <ArrowRight size={17} aria-hidden="true" />
        </button>
        {error && <p className="auth-error" role="alert">{error}</p>}
        <p className="auth-notice">
          登录会打开独立的账号验证窗口。关闭窗口可取消登录。
        </p>
      </section>
    </main>
  </div>
}
