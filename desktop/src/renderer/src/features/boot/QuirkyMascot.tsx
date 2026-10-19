/**
 * @file QuirkyMascot.tsx
 * @description 搞怪可爱的极简矢量动画角色组件（科研打工小怪兽）。
 * 采用纯 SVG + CSS 硬件加速关键帧动效，具备果冻弹跳、疯狂敲键盘、斗鸡眼打转、
 * 灵感天线摇晃与咖啡热气等生动幽默的微动效，取代枯燥的技术文字提示。
 * @author tongliga
 * @date 2026-10-19
 */

interface QuirkyMascotProps {
  /** 是否处于异常错误状态（眼睛变成蚊香眼、天线冒烟） */
  error?: boolean
}

export function QuirkyMascot({ error = false }: QuirkyMascotProps) {
  return (
    <div className={`quirky-mascot-box ${error ? 'is-error' : ''}`} aria-hidden="true">
      <svg
        className="quirky-mascot-svg"
        viewBox="0 0 240 180"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          {/* 果冻身体渐变 */}
          <linearGradient id="blobGrad" x1="90" y1="40" x2="150" y2="140" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor={error ? '#f87171' : '#6875f5'} />
            <stop offset="100%" stopColor={error ? '#dc2626' : '#4f46e5'} />
          </linearGradient>

          {/* 屏幕霓虹光渐变 */}
          <linearGradient id="screenGrad" x1="102" y1="118" x2="138" y2="142" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#06b6d4" />
            <stop offset="100%" stopColor="#8b5cf6" />
          </linearGradient>

          {/* 阴影渐变 */}
          <radialGradient id="shadowGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(30, 27, 75, 0.28)" />
            <stop offset="100%" stopColor="rgba(30, 27, 75, 0)" />
          </radialGradient>
        </defs>

        {/* 底部动态投影 */}
        <ellipse className="mascot-ground-shadow" cx="120" cy="154" rx="42" ry="7" fill="url(#shadowGrad)" />

        {/* 桌面背景小元素：搞怪咖啡杯 */}
        <g className="mascot-coffee">
          {/* 咖啡杯身 */}
          <rect x="42" y="128" width="16" height="18" rx="3" fill="#cbd5e1" />
          <path d="M58 132 C62 132 62 140 58 140" stroke="#cbd5e1" strokeWidth="2" strokeLinecap="round" fill="none" />
          {/* 杯中咖啡 */}
          <ellipse cx="50" cy="129" rx="6" ry="1.5" fill="#78350f" />
          {/* 搞怪咖啡热气 */}
          <path className="coffee-steam steam-1" d="M47 124 C45 120 50 116 48 112" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" fill="none" />
          <path className="coffee-steam steam-2" d="M53 123 C55 119 51 115 54 111" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" fill="none" />
        </g>

        {/* 笔记本电脑 */}
        <g className="mascot-laptop">
          {/* 屏幕 */}
          <rect className="laptop-screen" x="100" y="112" width="40" height="26" rx="3" fill="#0f172a" stroke="#334155" strokeWidth="1.5" />
          <rect x="103" y="115" width="34" height="20" rx="1.5" fill="url(#screenGrad)" opacity="0.85" />
          {/* 屏幕代码飞速跑动流 */}
          <line className="code-line line-1" x1="106" y1="119" x2="122" y2="119" stroke="#ffffff" strokeWidth="1.5" strokeLinecap="round" />
          <line className="code-line line-2" x1="106" y1="124" x2="132" y2="124" stroke="#e0e7ff" strokeWidth="1.5" strokeLinecap="round" />
          <line className="code-line line-3" x1="106" y1="129" x2="118" y2="129" stroke="#67e8f9" strokeWidth="1.5" strokeLinecap="round" />
          {/* 键盘底座 */}
          <path d="M94 144 L146 144 L141 149 L99 149 Z" fill="#475569" stroke="#334155" strokeWidth="1" />
        </g>

        {/* 敲键盘蹦出的搞怪代码粒子 */}
        <g className="mascot-sparks">
          <text className="spark spark-1" x="90" y="125" fontSize="9" fontWeight="bold" fill="#60a5fa">0</text>
          <text className="spark spark-2" x="142" y="120" fontSize="9" fontWeight="bold" fill="#34d399">1</text>
          <text className="spark spark-3" x="84" y="110" fontSize="11" fill="#fbbf24">✦</text>
          <text className="spark spark-4" x="148" y="108" fontSize="10" fill="#f472b6">⚡</text>
        </g>

        {/* 核心搞怪主体：果冻小怪物 */}
        <g className="mascot-blob-root">
          {/* 脑袋上的灵感天线 */}
          <g className="mascot-antenna">
            {/* 弹簧杆 */}
            <path d="M120 48 Q124 40 118 36 T122 28" stroke={error ? '#f87171' : '#a5b4fc'} strokeWidth="2.5" strokeLinecap="round" fill="none" />
            {/* 搞怪灯泡 / 小星光 */}
            {error ? (
              <g className="broken-sparks">
                <circle cx="122" cy="25" r="4.5" fill="#f87171" />
                <path d="M117 20 L127 30 M127 20 L117 30" stroke="#fee2e2" strokeWidth="1.5" />
              </g>
            ) : (
              <g className="antenna-bulb">
                <circle cx="122" cy="25" r="5" fill="#fde047" />
                <circle cx="122" cy="25" r="9" fill="#fef08a" opacity="0.4" className="bulb-glow" />
              </g>
            )}
          </g>

          {/* 果冻身体 */}
          <path
            className="mascot-body"
            d="M92 88 C92 56 104 46 120 46 C136 46 148 56 148 88 C148 120 144 140 120 140 C96 140 92 120 92 88 Z"
            fill="url(#blobGrad)"
          />

          {/* 腮红 */}
          <ellipse cx="102" cy="94" rx="4.5" ry="2.5" fill="#fda4af" opacity="0.65" />
          <ellipse cx="138" cy="94" rx="4.5" ry="2.5" fill="#fda4af" opacity="0.65" />

          {/* 眼睛区域 */}
          {error ? (
            /* 异常时：搞怪蚊香眼 (x_x) */
            <g className="eyes-error">
              <path d="M102 78 L114 90 M114 78 L102 90" stroke="#ffffff" strokeWidth="2.5" strokeLinecap="round" />
              <path d="M126 78 L138 90 M138 78 L126 90" stroke="#ffffff" strokeWidth="2.5" strokeLinecap="round" />
            </g>
          ) : (
            /* 正常时：大小不一的大圆眼 + 疯狂转动眼珠 */
            <g className="eyes-normal">
              {/* 左眼：偏大 */}
              <circle cx="108" cy="82" r="10.5" fill="#ffffff" />
              <g className="eye-pupil pupil-left">
                <circle cx="110" cy="83" r="5" fill="#1e1b4b" />
                <circle cx="108.5" cy="80.5" r="1.8" fill="#ffffff" />
              </g>

              {/* 右眼：稍小、逗趣斗鸡感 */}
              <circle cx="132" cy="81" r="8.5" fill="#ffffff" />
              <g className="eye-pupil pupil-right">
                <circle cx="130.5" cy="81.5" r="4.2" fill="#1e1b4b" />
                <circle cx="129.5" cy="79.5" r="1.5" fill="#ffffff" />
              </g>

              {/* 搞怪逗趣微汗滴 */}
              <path
                className="sweat-drop"
                d="M142 66 C142 66 146 72 144 74 C142 76 139 74 140 71 Z"
                fill="#38bdf8"
              />
            </g>
          )}

          {/* 搞怪嘴巴 */}
          {error ? (
            /* 挂掉时的波浪嘴 */
            <path d="M112 104 Q116 100 120 104 T128 104" stroke="#ffffff" strokeWidth="2" strokeLinecap="round" fill="none" />
          ) : (
            /* 正在拼命努力的惊讶开合小嘴 */
            <ellipse className="mascot-mouth" cx="120" cy="98" rx="3.5" ry="4.5" fill="#1e1b4b" />
          )}

          {/* 疯狂敲键盘的搞怪面条手臂 */}
          <g className="mascot-arms">
            {/* 左手面条手 */}
            <path
              className="noodle-arm arm-left"
              d="M92 98 Q80 126 105 138"
              stroke={error ? '#f87171' : '#6875f5'}
              strokeWidth="5"
              strokeLinecap="round"
              fill="none"
            />
            {/* 右手面条手 */}
            <path
              className="noodle-arm arm-right"
              d="M148 98 Q160 126 135 138"
              stroke={error ? '#f87171' : '#4f46e5'}
              strokeWidth="5"
              strokeLinecap="round"
              fill="none"
            />
          </g>
        </g>
      </svg>
    </div>
  )
}
