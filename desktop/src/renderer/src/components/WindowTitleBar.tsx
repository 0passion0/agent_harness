import { useEffect } from 'react'

export function WindowTitleBar({ theme }: { theme: 'light' | 'dark' }) {
  useEffect(() => {
    void window.medpeer?.setWindowTheme(theme).catch(console.error)
  }, [theme])

  return <header className="window-titlebar" aria-label="窗口标题栏">
    <span>MedPeer</span>
  </header>
}
