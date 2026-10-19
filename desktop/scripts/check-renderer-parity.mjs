import { createHash } from 'node:crypto'
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const sourceDesktop = process.argv[2]
if (!sourceDesktop) throw new Error('Pass the source desktop directory as the first argument')

const targetDesktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const roots = ['src/renderer', 'src/preload/index.ts', 'src/shared/protocol.ts']

async function files(root, relative = '') {
  const full = path.join(root, relative)
  const entries = await readdir(full, { withFileTypes: true })
  const result = []
  for (const entry of entries) {
    if (entry.name === 'agent.md') continue
    const child = path.join(relative, entry.name)
    if (entry.isDirectory()) result.push(...await files(root, child))
    else result.push(child)
  }
  return result
}

const adaptations = {
  'src/shared/protocol.ts': [["provider: 'medpeer'", "provider: 'medpeer' | 'deepseek' | 'harness'"]],
  'src/renderer/src/state/agent-state.ts': [
    ["  | { event: 'run.stopping' | 'interaction.answered' | 'error.clear' }", "  | { event: 'run.stopping' | 'error.clear' }\n  | { event: 'interaction.answered'; interaction_id: string }"],
    ["    case 'run.stopping':\n", "    case 'run.stopping':\n      if (!state.runId) return state\n"],
    ["    case 'interaction.answered':\n", "    case 'interaction.answered':\n      if (state.question?.interaction_id !== action.interaction_id) return state\n"]
  ],
  'src/renderer/src/state/use-agent-controller.ts': [["dispatch({ event: 'interaction.answered' })", "dispatch({ event: 'interaction.answered', interaction_id: interactionId })"]]
}
async function hash(file, relative, source = false) {
  if (!/\.(tsx?|css|html|svg)$/.test(file)) return createHash('sha256').update(await readFile(file)).digest('hex')
  let content = (await readFile(file, 'utf8')).replaceAll('\r\n', '\n')
  if (source) for (const [before, after] of adaptations[relative] ?? []) {
    if (!content.includes(before)) throw new Error(`Source adaptation no longer applies: ${relative}`)
    content = content.replace(before, after)
  }
  return createHash('sha256').update(content).digest('hex')
}

const sourceFiles = new Set()
const targetFiles = new Set()
for (const root of roots) {
  if (root.endsWith('.ts')) {
    sourceFiles.add(root)
    targetFiles.add(root)
  } else {
    for (const file of await files(path.join(sourceDesktop, root))) sourceFiles.add(path.posix.join(root, file.replaceAll(path.sep, '/')))
    for (const file of await files(path.join(targetDesktop, root))) targetFiles.add(path.posix.join(root, file.replaceAll(path.sep, '/')))
  }
}

const differences = []
for (const file of [...new Set([...sourceFiles, ...targetFiles])].sort()) {
  if (!sourceFiles.has(file) || !targetFiles.has(file)) {
    differences.push(`${sourceFiles.has(file) ? 'missing target' : 'extra target'}: ${file}`)
  } else if (await hash(path.join(sourceDesktop, file), file, true) !== await hash(path.join(targetDesktop, file), file)) {
    differences.push(`hash mismatch: ${file}`)
  }
}

if (differences.length) {
  console.error(differences.join('\n'))
  process.exitCode = 1
} else {
  console.log(`Renderer parity passed: ${sourceFiles.size} files match; only 3 declared protocol/state adaptations applied (line endings normalized, agent.md excluded).`)
}
