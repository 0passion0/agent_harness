/** 按账号隔离的桌面项目和会话索引；对话正文由原有 Checkpointer 持久化。 */
import { createHash, randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, realpathSync, renameSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { ApplicationState, SessionItem } from '../shared/protocol'
type Project = ApplicationState['projects'][number]

type Session = SessionItem & { project_id: string }
type Index = { projects: Project[]; sessions: Session[]; active_project_id: string | null; active_sessions: Record<string, string> }

/** 在同目录完成临时文件写入后再发布新索引。 */
export function writeAtomic(file: string, data: string | Buffer): void {
  mkdirSync(path.dirname(file), { recursive: true })
  const temporary = `${file}.tmp`
  writeFileSync(temporary, data, { mode: 0o600 })
  renameSync(temporary, file)
}

/** 保存一个已验证账号的项目和线程归属，所有路径均由宿主生成。 */
export class LocalStore {
  readonly directory: string
  private readonly file: string
  private index: Index

  constructor(dataDirectory: string, userId: number) {
    this.directory = path.join(dataDirectory, 'accounts', createHash('sha256').update(String(userId)).digest('hex'))
    this.file = path.join(this.directory, 'index.json')
    this.index = existsSync(this.file)
      ? JSON.parse(readFileSync(this.file, 'utf8')) as Index
      : { projects: [], sessions: [], active_project_id: null, active_sessions: {} }
  }

  get projects(): Project[] { return this.index.projects }
  get activeProject(): Project | undefined { return this.index.projects.find(project => project.id === this.index.active_project_id) }
  private save(): void { writeAtomic(this.file, JSON.stringify(this.index, null, 2)) }

  createProject(name: string): Project {
    const project = { id: randomUUID(), name, path: null }
    this.index.projects.push(project)
    this.index.active_project_id = project.id
    this.save()
    return project
  }

  selectProject(id: string): void {
    if (!this.index.projects.some(project => project.id === id)) throw new Error('项目不属于当前账号')
    this.index.active_project_id = id
    this.save()
  }

  bindProject(id: string, projectPath: string): Project {
    const project = this.index.projects.find(item => item.id === id)
    if (!project) throw new Error('项目不属于当前账号')
    if (!statSync(projectPath).isDirectory()) throw new Error('请选择文件夹')
    project.path = realpathSync(projectPath)
    this.save()
    return project
  }

  projectDirectory(): string {
    if (!this.activeProject) throw new Error('请先创建或选择项目')
    return path.join(this.directory, 'projects', this.activeProject.id)
  }

  sessions(query = ''): SessionItem[] {
    if (!this.activeProject) return []
    return this.index.sessions.filter(session => session.project_id === this.activeProject!.id && session.title.includes(query))
      .sort((a, b) => b.updated.localeCompare(a.updated))
      .map(({ id, title, updated }) => ({ id, title, updated }))
  }

  session(id: string): Session {
    const session = this.index.sessions.find(item => item.id === id && item.project_id === this.activeProject?.id)
    if (!session) throw new Error('会话不属于当前项目或账号')
    return session
  }

  currentSession(): Session {
    if (!this.activeProject) throw new Error('请先创建或选择项目')
    const id = this.index.active_sessions[this.activeProject.id]
    return id ? this.session(id) : this.createSession()
  }

  createSession(): Session {
    if (!this.activeProject) throw new Error('请先创建或选择项目')
    const session = { id: randomUUID(), project_id: this.activeProject.id, title: '新会话', updated: new Date().toISOString() }
    this.index.sessions.push(session)
    this.index.active_sessions[this.activeProject.id] = session.id
    this.save()
    return session
  }

  resume(id: string): void {
    const session = this.session(id)
    this.index.active_sessions[session.project_id] = id
    this.save()
  }

  rename(id: string, title: string): void {
    this.session(id).title = title
    this.save()
  }

  deleteSession(id: string): SessionItem[] {
    const session = this.session(id)
    this.index.sessions = this.index.sessions.filter(item => item.id !== session.id)
    if (this.index.active_sessions[session.project_id] === id) delete this.index.active_sessions[session.project_id]
    this.save()
    return this.sessions()
  }

  touch(id: string, text?: string): void {
    const session = this.session(id)
    if (text && session.title === '新会话') session.title = text.slice(0, 60)
    session.updated = new Date().toISOString()
    this.save()
  }
}
