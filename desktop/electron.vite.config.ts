/**
 * @file electron.vite.config.ts
 * @description Electron Vite 构建与打包配置文件。
 * 分别针对主进程（main）、预加载脚本（preload）与渲染进程（renderer）进行打包配置：
 * 1. 主进程：默认配置；
 * 2. 预加载脚本：输出格式配置为 CommonJS（CJS），文件命名规则为 [name].cjs，确保与 Electron 上下文隔离无缝集成；
 * 3. 渲染进程：启用 @vitejs/plugin-react 支持 React 18 JSX 与快速刷新。
 * @author tongliga
 * @date 2026-10-16
 */

import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // 主进程配置
  main: {},
  // 预加载脚本配置
  preload: {
    build: {
      rollupOptions: {
        output: {
          format: 'cjs',
          entryFileNames: '[name].cjs'
        }
      }
    }
  },
  // 渲染进程配置
  renderer: {
    plugins: [react()]
  }
})

