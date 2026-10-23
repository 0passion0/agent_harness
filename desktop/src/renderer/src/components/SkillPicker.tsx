import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Blocks, Search, X } from 'lucide-react'
import type { ConfigScope, HarnessConfig } from '../../../shared/protocol'

type Skill = HarnessConfig['skills'][number] & { scope: ConfigScope }

export function SkillPicker({ projectId, draft, disabled, onSelect, onClose, onManage }: {
  projectId: string
  draft: string
  disabled: boolean
  onSelect(name: string): void
  onClose(): void
  onManage(): void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [active, setActive] = useState(0)
  const picker = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const options = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (draft === '/' && !disabled) setOpen(true)
  }, [draft, disabled])

  useEffect(() => {
    setOpen(false)
    setSkills([])
  }, [projectId, disabled])

  useEffect(() => {
    if (!open) return
    let current = true
    setQuery('')
    setActive(0)
    setLoading(true)
    setError('')
    search.current?.focus()
    const scopes: ConfigScope[] = ['global', 'project']
    void Promise.allSettled(scopes.map(async scope => {
      const config = await window.medpeer.command({ id: crypto.randomUUID(), method: 'harness.inspect', params: { scope, project_id: scope === 'project' ? projectId : '' } }) as HarnessConfig
      return config.skills.map(skill => ({ ...skill, scope }))
    })).then(results => {
      if (!current) return
      // Project skills take precedence over system skills with the same name.
      const available = new Map<string, Skill>()
      results.forEach(result => {
        if (result.status === 'fulfilled') result.value.forEach(skill => available.set(skill.name, skill))
      })
      setSkills([...available.values()])
      setError(results.filter(result => result.status === 'rejected').map(result => String(result.reason)).join('\n'))
      setLoading(false)
    })
    const dismiss = (event: PointerEvent) => {
      if (!picker.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    return () => { current = false; document.removeEventListener('pointerdown', dismiss) }
  }, [open, projectId])

  useEffect(() => {
    options.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active, query])

  const filtered = loading ? [] : skills.filter(skill => `${skill.name} ${skill.description}`.toLowerCase().includes(query.toLowerCase()))
  const close = () => { setOpen(false); onClose() }
  const select = (skill: Skill) => { setOpen(false); onSelect(skill.name) }

  return <div className="skill-picker" ref={picker}>
    <button type="button" className="composer-skill-trigger" disabled={disabled} aria-label="选择技能" aria-expanded={open} aria-controls="composer-skills" onClick={() => setOpen(value => !value)}>
      <Blocks size={15} /><span>技能</span><kbd>/</kbd>
    </button>
    {open && <section id="composer-skills" className="skill-menu" aria-label="技能选择">
      <header><span><Blocks size={15} />使用技能</span><button type="button" aria-label="关闭技能菜单" onClick={close}><X size={15} /></button></header>
      <div className="skill-menu-search"><Search size={15} /><input
        ref={search} value={query} placeholder="搜索技能名称或用途…" aria-label="搜索技能" role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls="skill-options" aria-activedescendant={filtered[active] ? `skill-option-${active}` : undefined}
        onChange={event => { setQuery(event.target.value); setActive(0) }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return
          if (['ArrowDown', 'ArrowUp', 'Enter', 'Escape'].includes(event.key)) { event.preventDefault(); event.stopPropagation() }
          if (event.key === 'Escape') close()
          if (event.key === 'ArrowDown') setActive(value => Math.max(0, Math.min(value + 1, filtered.length - 1)))
          if (event.key === 'ArrowUp') setActive(value => Math.max(0, value - 1))
          if (event.key === 'Enter' && filtered[active]) select(filtered[active])
        }}
      /></div>
      <div className="skill-menu-options" id="skill-options" role="listbox" aria-label="可用技能" ref={options} aria-busy={loading}>
        {loading ? <p className="skill-menu-empty" role="status">正在读取技能…</p> : filtered.map((skill, index) => <div key={skill.name} id={`skill-option-${index}`} role="option" aria-selected={index === active} className="skill-menu-option" onPointerMove={() => setActive(index)} onMouseDown={event => event.preventDefault()} onClick={() => select(skill)}>
          <Blocks size={16} /><span><strong>{skill.name}</strong><small>{skill.description || '按技能中的指引完成任务'}</small></span><em>{skill.scope === 'project' ? '项目' : '系统'}</em>
        </div>)}
        {!loading && !filtered.length && <p className="skill-menu-empty">{query ? '没有找到匹配的技能' : '还没有技能，可在技能设置中导入。'}</p>}
      </div>
      {error && <p className="skill-menu-error" role="alert">部分技能读取失败：{error}</p>}
      <footer><span>↑ ↓ 选择 · Enter 使用 · Esc 关闭</span><button type="button" onClick={() => { setOpen(false); onManage() }}>管理技能<ArrowUpRight size={12} /></button></footer>
    </section>}
  </div>
}
