import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Plus, X } from 'lucide-react'
import type { ApplicationState } from '../../../shared/protocol'

type Props = {
  application: ApplicationState | null
  plugins: string[]
  skills: string[]
  setPlugins(ids: string[]): void
  setSkills(ids: string[]): void
  disabled?: boolean
  onEscape?(): void
}

export type CapabilityPickerHandle = { open(): void; close(): void; isOpen(): boolean }

export function isCapabilityShortcut(event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey'>): boolean {
  return event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey
}

export const CapabilityPicker = forwardRef<CapabilityPickerHandle, Props>(function CapabilityPicker({ application, plugins, skills, setPlugins, setSkills, disabled = false, onEscape }, ref) {
  const [menu, setMenu] = useState<'closed' | 'choose' | 'plugins' | 'skills'>('closed')
  const firstMenuItem = useRef<HTMLButtonElement>(null)
  const changeMenu = (next: typeof menu) => {
    setMenu(next)
  }
  useImperativeHandle(ref, () => ({ open: () => changeMenu('choose'), close: () => changeMenu('closed'), isOpen: () => menu !== 'closed' }))
  useEffect(() => { if (disabled) changeMenu('closed') }, [disabled])
  useEffect(() => { if (menu !== 'closed') firstMenuItem.current?.focus() }, [menu])
  const availablePlugins = application?.plugins.filter(item => item.enabled) ?? []
  const availableSkills = application?.skills.filter(item => item.enabled && (!item.plugin_id || availablePlugins.some(plugin => plugin.id === item.plugin_id))) ?? []
  const selectedPlugins = availablePlugins.filter(item => plugins.includes(item.id))
  const selectedSkills = availableSkills.filter(item => skills.includes(item.id))
  return <div className="capability-picker">
    <button type="button" className="attach-control" disabled={disabled} aria-label="选择插件或技能" onClick={() => changeMenu(menu === 'closed' ? 'choose' : 'closed')}><Plus size={16} /></button>
    {selectedPlugins.map(item => <button type="button" className="capability-chip" key={item.id} onClick={() => setPlugins(plugins.filter(id => id !== item.id))}>{item.name}<X size={12}/></button>)}
    {selectedSkills.map(item => <button type="button" className="capability-chip" key={item.id} onClick={() => setSkills(skills.filter(id => id !== item.id))}>{item.name}<X size={12}/></button>)}
    {menu !== 'closed' && <div className="capability-menu" role="menu" onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); const next = menu === 'choose' ? 'closed' : 'choose'; changeMenu(next); if (next === 'closed') onEscape?.() } }}>
      {menu === 'choose' ? <>
        <button ref={firstMenuItem} role="menuitem" onClick={() => changeMenu('plugins')}>插件 <span>{selectedPlugins.length}</span></button>
        <button role="menuitem" onClick={() => changeMenu('skills')}>技能 <span>{selectedSkills.length}</span></button>
      </> : <>
        <button ref={firstMenuItem} role="menuitem" onClick={() => changeMenu('choose')}>‹ 返回</button>
        {(menu === 'plugins' ? availablePlugins : availableSkills).map(item => {
          const selected = menu === 'plugins' ? plugins.includes(item.id) : skills.includes(item.id)
          const toggle = () => menu === 'plugins'
            ? setPlugins(selected ? plugins.filter(id => id !== item.id) : [...plugins, item.id])
            : setSkills(selected ? skills.filter(id => id !== item.id) : [...skills, item.id])
          return <button role="menuitemcheckbox" aria-checked={selected} key={item.id} onClick={toggle}>
            <span>{selected ? '✓ ' : ''}{item.name}</span>
            <small>{menu === 'skills' ? (item as typeof availableSkills[number]).source : (item as typeof availablePlugins[number]).version}</small>
          </button>
        })}
      </>}
      <button className="capability-done" onClick={() => changeMenu('closed')}>完成</button>
    </div>}
  </div>
})
