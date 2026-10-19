/**
 * @file main.tsx
 * @description React 渲染进程启动根入口。
 * 挂载 React 19 根节点，引入全局样式表，并在 StrictMode 下渲染主应用外壳 App 组件。
 * @author tongliga
 * @date 2026-10-16
 */

import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

// 获取 DOM 挂载容器并启动 React 渲染流程
const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('未找到根节点 #root')

createRoot(rootElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
