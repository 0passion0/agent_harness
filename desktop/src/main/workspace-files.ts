/**
 * @file workspace-files.ts
 * @description 工作区本地文件系统扫描、状态读取与文件复制模块。
 * 遵循极简原则，基于 Node.js 原生 fs/promises 实现目录树遍历、深度控制、忽略规则过滤及外部文件导入。
 * @author tongliga
 * @date 2026-10-16
 */

import { readdir, stat, copyFile } from 'node:fs/promises'
import { constants, existsSync } from 'node:fs'
import path from 'node:path'
import type { WorkspaceFileEntry } from '../shared/protocol'

/**
 * 排除在工作区目录树中展示的缓存、版本控制与环境配置项白名单集合。
 */
const IGNORED_NAMES = new Set([
  '.git',
  'node_modules',
  '.venv',
  '__pycache__',
  '.medpeer',
  '.idea',
  '.vscode',
  '.DS_Store',
  'Thumbs.db',
  'dist',
  'build',
  '.pytest_cache'
])

/**
 * 递归扫描指定工作区目录下的文件与子目录树。
 *
 * 第一步：检查输入路径有效性，若目录不存在则直接返回空数组。
 * 第二步：深度优先遍历目录项，过滤被忽略的名称或隐藏文件。
 * 第三步：读取每个条目的详细属性（是否目录、大小、修改时间），排序后返回。
 *
 * @param rootPath - 工作区根路径绝对地址
 * @param maxDepth - 最大递归深度限制，默认 3 层（防止深度嵌套耗时）
 * @param maxEntries - 最大文件总数限制，默认 300（防止大目录阻塞渲染）
 * @returns 扫描得到的结构化工作区文件与目录列表
 */
export async function scanDirectory(
  rootPath: string,
  maxDepth = 3,
  maxEntries = 300
): Promise<WorkspaceFileEntry[]> {
  // 第一步：参数有效性与存在性校验
  if (!rootPath || !existsSync(rootPath)) return []
  const results: WorkspaceFileEntry[] = []

  // 第二步：深度优先递归扫描内部函数
  async function walk(currentPath: string, depth: number): Promise<void> {
    if (depth > maxDepth || results.length >= maxEntries) return
    try {
      const entries = await readdir(currentPath, { withFileTypes: true })
      // 排序规则：目录在前，文件在后；同类项按文件名拼音/字母排序
      entries.sort((a, b) => {
        if (a.isDirectory() === b.isDirectory()) return a.name.localeCompare(b.name, 'zh-CN')
        return a.isDirectory() ? -1 : 1
      })
      for (const entry of entries) {
        if (results.length >= maxEntries) break
        // 过滤系统及缓存文件夹，跳过以点开头的隐藏项
        if (IGNORED_NAMES.has(entry.name) || entry.name.startsWith('.')) continue
        const fullPath = path.join(currentPath, entry.name)
        const relPath = path.relative(rootPath, fullPath).replace(/\\/g, '/')
        try {
          const stats = await stat(fullPath)
          results.push({
            name: entry.name,
            path: fullPath,
            relativePath: relPath,
            isDirectory: entry.isDirectory(),
            size: stats.size,
            mtime: stats.mtimeMs
          })
          if (entry.isDirectory()) {
            await walk(fullPath, depth + 1)
          }
        } catch {
          // 跳过无权限访问或临时已被删除的条目
        }
      }
    } catch {
      // 跳过无权限访问的子目录
    }
  }

  // 第三步：从根目录开始递归遍历并返回扁平结构化列表
  await walk(rootPath, 1)
  return results
}

/**
 * 将外部文件安全复制到目标工作区根目录中。
 *
 * 第一步：检查目标工作区目录是否存在，不存在则抛出异常。
 * 第二步：逐一检查源文件路径是否存在并复制到目标目录根路径。
 * 第三步：返回已成功复制的文件名称列表。
 *
 * @param sourcePaths - 待导入的文件物理路径数组
 * @param targetDir - 目标工作区目录路径
 * @returns 成功复制的文件名称数组
 * @throws Error 当目标工作区目录不存在时抛出
 */
export async function copyFilesToWorkspace(
  sourcePaths: string[],
  targetDir: string
): Promise<string[]> {
  // 第一步：目标目录存在性校验
  if (!targetDir || !existsSync(targetDir)) {
    throw new Error('目标工作区目录不存在')
  }
  const names = sourcePaths.map(src => path.basename(src))
  if (new Set(names.map(name => name.toLowerCase())).size !== names.length) throw new Error('导入文件不能重名')
  for (let i = 0; i < sourcePaths.length; i++) {
    if (!(await stat(sourcePaths[i])).isFile()) throw new Error('仅支持文件')
    if (existsSync(path.join(targetDir, names[i]))) throw new Error(`文件已存在：${names[i]}`)
  }
  for (let i = 0; i < sourcePaths.length; i++) await copyFile(sourcePaths[i], path.join(targetDir, names[i]), constants.COPYFILE_EXCL)
  return names
}
