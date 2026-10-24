/**
 * @file local-store.ts
 * @description 本地项目与历史会话索引存储模块。
 * 维护本地项目列表、当前激活项目、会话元数据（标题、创建与更新时间），
 * 并提供会话检索、重命名、删除及原子写入保护。对话正文由底层 Checkpointer 持久化。
 * @author tongliga
 * @date 2026-10-16
 */

import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, realpathSync, renameSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { ApplicationState, SessionItem } from '../shared/protocol'

/**
 * 项目概要信息类型。
 */
type Project = ApplicationState['projects'][number]

/**
 * 带有归属项目 ID 的内部会话条目结构。
 */
type Session = SessionItem & { project_id: string }

/**
 * 本地 index.json 文件保存的完整索引结构契约。
 */
type Index = {
  /** 已创建的项目列表 */
  projects: Project[]
  /** 全部历史会话列表 */
  sessions: Session[]
  /** 当前处于激活选中状态的项目 ID */
  active_project_id: string | null
  /** 各项目最后激活的会话 ID 映射（key 为 project_id） */
  active_sessions: Record<string, string>
}

/**
 * 原子安全文件写入工具函数。
 * 先在目标文件所在同级目录写入 `.tmp` 临时文件，完成后再重命名覆盖目标，
 * 有效防止掉电或并发读取到半截未写完的数据。
 *
 * @param file - 目标文件的绝对物理路径
 * @param data - 待写入的字符串或二进制数据
 */
export function writeAtomic(file: string, data: string | Buffer): void {
  mkdirSync(path.dirname(file), { recursive: true })
  const temporary = `${file}.tmp`
  writeFileSync(temporary, data, { mode: 0o600 })
  renameSync(temporary, file)
}

/**
 * 本地项目与会话数据索引管理器。
 */
export class LocalStore {
  /** 本地数据根目录绝对物理地址 */
  readonly directory: string
  /** 索引文件 index.json 绝对物理路径 */
  private readonly file: string
  /** 内存中缓存的完整索引数据 */
  private index: Index

  /**
   * 初始化本地存储实例。
   *
   * @param directory - 本地存储根目录物理绝对路径
   */
  constructor(directory: string) {
    this.directory = path.resolve(directory)
    this.file = path.join(this.directory, 'index.json')
    this.index = existsSync(this.file)
      ? JSON.parse(readFileSync(this.file, 'utf8')) as Index
      : { projects: [], sessions: [], active_project_id: null, active_sessions: {} }
  }

  /**
   * 获取已创建的所有项目列表。
   */
  get projects(): Project[] {
    return this.index.projects
  }

  /**
   * 获取当前处于激活状态的项目对象，未选择任何项目时返回 undefined。
   */
  get activeProject(): Project | undefined {
    return this.index.projects.find(project => project.id === this.index.active_project_id)
  }

  /**
   * 将当前内存中的索引数据以原子方式持久化到本地 index.json。
   */
  private save(): void {
    writeAtomic(this.file, JSON.stringify(this.index, null, 2))
  }

  /**
   * 创建一个新项目并自动设为当前激活项目。
   *
   * @param name - 新建项目名称
   * @returns 新建成功的项目对象
   */
  createProject(name: string): Project {
    const project = { id: randomUUID(), name, path: null }
    this.index.projects.push(project)
    this.index.active_project_id = project.id
    this.save()
    return project
  }

  /**
   * 切换当前激活的项目。
   *
   * @param id - 待激活的项目 ID
   * @throws Error 当指定 ID 的项目不存在时抛出
   */
  selectProject(id: string): void {
    if (!this.index.projects.some(project => project.id === id)) throw new Error('项目不存在')
    this.index.active_project_id = id
    this.save()
  }

  /**
   * 将本地文件系统中的目录绑定至指定项目作为工作区。
   *
   * @param id - 目标项目 ID
   * @param projectPath - 本地文件夹物理绝对路径
   * @returns 绑定成功后的项目对象
   * @throws Error 当项目不存在或路径不是合法文件夹时抛出
   */
  bindProject(id: string, projectPath: string): Project {
    const project = this.index.projects.find(item => item.id === id)
    if (!project) throw new Error('项目不存在')
    if (!statSync(projectPath).isDirectory()) throw new Error('请选择文件夹')
    project.path = realpathSync(projectPath)
    this.save()
    return project
  }

  /**
   * 获取当前激活项目的专属本地运行时数据存储目录路径。
   *
   * @returns 项目数据目录物理绝对路径
   * @throws Error 当尚未选择任何项目时抛出
   */
  projectDirectory(projectId = this.activeProject?.id): string {
    if (!this.index.projects.some(project => project.id === projectId)) throw new Error('请先创建或选择项目')
    return path.join(this.directory, 'projects', projectId!)
  }

  /**
   * 获取当前激活项目下的所有历史会话列表，支持按关键词过滤并按更新时间倒序排列。
   *
   * @param query - 可选的标题过滤搜索词
   * @returns 会话列表数组
   */
  sessions(query = ''): SessionItem[] {
    if (!this.activeProject) return []
    return this.index.sessions.filter(session => session.project_id === this.activeProject!.id && session.title.includes(query))
      .sort((a, b) => b.updated.localeCompare(a.updated))
      .map(({ id, title, updated }) => ({ id, title, updated }))
  }

  /**
   * 检索获取当前项目下的指定会话详情对象。
   *
   * @param id - 会话 ID
   * @returns 会话对象
   * @throws Error 当会话不存在或不属于当前项目时抛出
   */
  session(id: string, projectId = this.activeProject?.id): Session {
    const session = this.index.sessions.find(item => item.id === id && item.project_id === projectId)
    if (!session) throw new Error('会话不属于当前项目')
    return session
  }

  /**
   * 获取当前项目最近处于打开状态的活跃会话，若无则自动新建一个新会话。
   *
   * @returns 当前活跃的会话对象
   * @throws Error 当尚未选择项目时抛出
   */
  currentSession(): Session {
    if (!this.activeProject) throw new Error('请先创建或选择项目')
    const id = this.index.active_sessions[this.activeProject.id]
    return id ? this.session(id) : this.createSession()
  }

  /**
   * 在当前项目下创建一个全新的空会话并切换为当前活跃会话。
   *
   * @returns 新建生成的会话对象
   * @throws Error 当尚未选择项目时抛出
   */
  createSession(): Session {
    if (!this.activeProject) throw new Error('请先创建或选择项目')
    const session = { id: randomUUID(), project_id: this.activeProject.id, title: '新会话', updated: new Date().toISOString() }
    this.index.sessions.push(session)
    this.index.active_sessions[this.activeProject.id] = session.id
    this.save()
    return session
  }

  /**
   * 将指定会话切换标记为当前项目活跃的会话。
   *
   * @param id - 会话 ID
   */
  resume(id: string): void {
    const session = this.session(id)
    this.index.active_sessions[session.project_id] = id
    this.save()
  }

  /**
   * 修改指定会话的展示标题并持久化。
   *
   * @param id - 会话 ID
   * @param title - 新的会话标题
   */
  rename(id: string, title: string): void {
    this.session(id).title = title
    this.save()
  }

  /**
   * 删除指定的历史会话记录并从索引中移除。
   *
   * @param id - 会话 ID
   * @returns 删除后剩余的会话列表数组
   */
  deleteSession(id: string): SessionItem[] {
    const session = this.session(id)
    this.index.sessions = this.index.sessions.filter(item => item.id !== session.id)
    if (this.index.active_sessions[session.project_id] === id) delete this.index.active_sessions[session.project_id]
    this.save()
    return this.sessions()
  }

  /**
   * 更新指定会话的最近活跃时间戳，若是默认“新会话”且提供了输入文本则截取作为首标题。
   *
   * @param id - 会话 ID
   * @param text - 用户首次输入的提示词文本
   */
  touch(id: string, text?: string, projectId = this.activeProject?.id): void {
    const session = this.session(id, projectId)
    if (text && session.title === '新会话') session.title = text.slice(0, 60)
    session.updated = new Date().toISOString()
    this.save()
  }
}

