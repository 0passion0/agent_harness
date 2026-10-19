/**
 * @file AccountMenu.tsx
 * @description 用户账户与研值余额弹出菜单组件。
 * 负责展示当前已登录用户的头像、姓名、用户名、系统账号 ID、绑定手机号以及研值余额，
 * 并提供安全的退出登录操作。
 * @author tongliga
 * @date 2026-10-16
 */

import { useEffect, useRef, useState } from 'react'
import { LogOut } from 'lucide-react'
import type { UserProfile } from '../../../../shared/protocol'
import './auth.css'

/**
 * 账户菜单组件属性接口。
 */
type AccountMenuProps = {
  /** 当前登录的用户个人资料对象 */
  user: UserProfile
  /** 发生操作异常时的错误提示回调 */
  onError(message: string): void
  /** 系统是否处于运行忙碌状态 */
  busy: boolean
}

/**
 * 导航栏个人账户头像与弹窗菜单组件。
 *
 * 第一步：监听外部点击 (mousedown) 与 Escape 键，自动收起下拉面板。
 * 第二步：展示用户基本资料（ID、手机号、研值余额）。
 * 第三步：提供注销退出按钮，调用主进程 logout 并拦截异常。
 *
 * @param props - 组件属性
 */
export function AccountMenu({ user, onError, busy }: AccountMenuProps) {
  const [open, setOpen] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // 监听点击弹窗外部或按 Escape 键自动关闭
  useEffect(() => {
    function dismiss(event: MouseEvent) {
      if (event.target instanceof Node && !ref.current?.contains(event.target)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [])

  /** 执行退出登录操作 */
  async function logout() {
    if (leaving || busy) return
    setLeaving(true)
    try {
      await window.medpeer.logout()
      setOpen(false)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLeaving(false)
    }
  }

  const avatar = user.userAvatar
  const name = user.realName || user.userName || '用户'

  return <div className="account-menu-wrap" ref={ref}>
    {/* 触发头像按钮 */}
    <button
      className="rail-avatar"
      aria-label="我的账户"
      title="我的账户"
      aria-expanded={open}
      aria-haspopup="true"
      onClick={() => setOpen(value => !value)}
    >
      {avatar ? <img src={avatar} alt="" /> : <span>{name.slice(0, 1)}</span>}
    </button>

    {/* 下拉账户卡片 */}
    {open && <section className="account-popover" aria-label="我的基础信息">
      <div className="account-popover-head">
        {avatar ? <img src={avatar} alt="" /> : <span>{name.slice(0, 1)}</span>}
        <div>
          <strong>{name}</strong>
          <small>{user.userName}</small>
        </div>
      </div>
      <dl>
        <div>
          <dt>账号 ID</dt>
          <dd>{user.userID}</dd>
        </div>
        <div>
          <dt>手机号</dt>
          <dd>{user.userPhone || '未提供'}</dd>
        </div>
        <div>
          <dt>研值余额</dt>
          <dd>{user.userCredit}</dd>
        </div>
      </dl>
      <button className="account-logout" disabled={leaving || busy} onClick={() => void logout()}>
        <LogOut size={15} />
        {leaving ? '正在退出…' : '退出登录'}
      </button>
    </section>}
  </div>
}
