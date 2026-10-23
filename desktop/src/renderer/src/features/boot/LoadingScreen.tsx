/**
 * @file LoadingScreen.tsx
 * @description MedPeer 启动屏与后端数据等待加载页面组件。
 * 针对用户“不要文字提示，要一个动态好看的搞怪图”的需求，以极其生动幽默的纯矢量
 * 搞怪打工小怪兽（疯狂敲键盘、斗鸡眼眼珠乱转、灵感天线弹跳、咖啡冒烟）取代繁琐枯燥的文本阶段说明。
 * 搭配极简光晕与现代流光进度条，实现极简美与逗趣体验的完美结合。
 * @author tongliga
 * @date 2026-10-19
 */

import { useState } from 'react'
import { FileText, RefreshCw } from 'lucide-react'
import { QuirkyMascot } from './QuirkyMascot'
import { WindowTitleBar } from '../../components/WindowTitleBar'
import './loading.css'

/**
 * 启动等待加载页面组件属性接口。
 */
export type LoadingScreenProps = {
  /** 界面深浅色主题外观 */
  theme: 'light' | 'dark'
  /** 启动连接异常错误信息 */
  error?: string | null
  /** 触发重新连接后端回调 */
  onRetry?: () => void
}

/**
 * 极简逗趣启动与后端连接等待界面组件。
 *
 * @param props - 组件属性
 */
export function LoadingScreen({ theme, error, onRetry }: LoadingScreenProps) {
  const [retrying, setRetrying] = useState(false)

  // 处理用户手动重试
  const handleRetry = async () => {
    if (retrying || !onRetry) return
    setRetrying(true)
    try {
      await onRetry()
    } finally {
      setTimeout(() => setRetrying(false), 600)
    }
  }

  // 打开系统日志查看
  const handleOpenLogs = () => {
    try {
      void window.medpeer?.openDataDirectory?.('logs')
    } catch {
      /* 忽略非桌面环境异常 */
    }
  }

  return (
    <div className={`app product-v2 product-v3 ${theme}`}>
      <WindowTitleBar theme={theme} />
      <main className="boot-page" role="status" aria-live="polite">
        {/* 背景动态呼吸微光 */}
        <div className="boot-ambient-glow" aria-hidden="true" />

        {/* 核心卡片 */}
        <section className="boot-card" aria-label="应用加载中">
          {/* 动态好看的搞怪小怪兽 */}
          <QuirkyMascot error={!!error} />

          {/* 异常错误时的诊断与重试 */}
          {error ? (
            <>
              <div className="boot-error-box" role="alert">
                <strong>服务未能成功连接：</strong>
                <div>{error}</div>
              </div>
              <div className="boot-actions">
                <button
                  type="button"
                  className="boot-retry-btn"
                  disabled={retrying}
                  onClick={() => void handleRetry()}
                >
                  <RefreshCw size={15} className={retrying ? 'spin' : ''} />
                  {retrying ? '正在重试…' : '重新连接'}
                </button>
                <button
                  type="button"
                  className="boot-log-btn"
                  title="打开桌面端日志目录"
                  onClick={handleOpenLogs}
                >
                  <FileText size={15} />
                  查看日志
                </button>
              </div>
            </>
          ) : (
            /* 正常加载态：极简流光流动滑轨，纯视觉沉浸，无任何啰嗦文字打扰 */
            <div className="boot-progress-track" aria-hidden="true">
              <div className="boot-progress-bar" />
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
