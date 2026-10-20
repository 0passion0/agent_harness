import { existsSync } from 'node:fs'
import path from 'node:path'

export const bundledPython = (resourcesPath: string): string => path.join(resourcesPath, 'python', 'python.exe')

export const selectScriptPython = (
  configuredPython: string,
  resourcesPath: string,
  fallback = '',
  preferBundled = true
): string => {
  const bundled = bundledPython(resourcesPath)
  return preferBundled && existsSync(bundled) ? bundled : configuredPython || fallback
}
