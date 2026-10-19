import { expect, it } from 'vitest'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { copyFilesToWorkspace } from '../src/main/workspace-files'

it('validates the whole import before copying and never overwrites workspace files', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'workspace-import-'))
  const workspace = path.join(root, 'workspace'); mkdirSync(workspace)
  const first = path.join(root, 'a.txt'), second = path.join(root, 'b.txt')
  writeFileSync(first, 'new'); writeFileSync(second, 'second'); writeFileSync(path.join(workspace, 'b.txt'), 'original')
  try {
    await expect(copyFilesToWorkspace([first, second], workspace)).rejects.toThrow('已存在')
    expect(readFileSync(path.join(workspace, 'b.txt'), 'utf8')).toBe('original')
    await expect(copyFilesToWorkspace([first, path.join(root, 'missing')], workspace)).rejects.toThrow()
    expect(await copyFilesToWorkspace([first], workspace)).toEqual(['a.txt'])
  } finally { rmSync(root, { recursive: true, force: true }) }
})
