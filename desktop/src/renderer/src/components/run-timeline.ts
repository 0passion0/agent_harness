import type { RunRecord, Snapshot } from '../../../shared/protocol'
import { recordText } from '../state/agent-state'
import { toolSteps } from './tool-activity'
import { contentText } from './content-text'

export type RunBlock =
  | { kind: 'text'; key: string; text: string; messageId?: string; complete: boolean }
  | { kind: 'tools'; key: string; records: RunRecord[] }

/** 工具完成事件更新原来所在的分组，不会跨过后续正文再次出现。 */
export function runTimeline(records: RunRecord[], tools: Record<string, RunRecord> = {}): RunBlock[] {
  const blocks: RunBlock[] = []
  const toolOwners = new Map<string, Extract<RunBlock, { kind: 'tools' }>>()
  const modelTexts = new Map<string, Extract<RunBlock, { kind: 'text' }>>()
  let currentText: Extract<RunBlock, { kind: 'text' }> | undefined
  const ordered: RunRecord[] = [...records, ...Object.entries(tools).map(([id, tool]) => ({ ...tool, call_id: id }))]

  for (const [index, record] of ordered.entries()) {
    if (['tool_start', 'tool_end', 'tool_error'].includes(record.kind)) {
      const id = record.call_id ?? record.operation_id ?? `tool-${index}`
      let owner = toolOwners.get(id)
      if (!owner) {
        const last = blocks.at(-1)
        owner = last?.kind === 'tools' ? last : { kind: 'tools', key: `tools-${id}`, records: [] }
        if (owner !== last) blocks.push(owner)
        toolOwners.set(id, owner)
      }
      owner.records.push(record)
      continue
    }
    if (record.agent_name && record.agent_name !== 'vertical-agent') continue
    if (record.kind === 'model_start') currentText = undefined
    if (record.kind !== 'text' && record.kind !== 'model_end') continue
    const text = recordText(record)
    const messageId = typeof record.message_id === 'string' ? record.message_id : undefined
    const id = record.operation_id ?? messageId
    const anonymousText = !record.operation_id && !currentText?.complete && blocks.at(-1)?.kind === 'text' ? currentText : undefined
    let block = (id ? modelTexts.get(id) : undefined) ?? anonymousText
    if (!block && text) {
      block = { kind: 'text', key: `text-${id ?? index}`, text: '', messageId, complete: false }
      blocks.push(block)
      if (id) modelTexts.set(id, block)
    }
    if (!block) continue
    block.text = record.kind === 'model_end' ? text || block.text : block.text + text
    block.messageId = messageId ?? block.messageId
    block.complete = record.kind === 'model_end'
    currentText = block
  }
  return blocks.map(block => block.kind === 'tools' ? { ...block, records: toolSteps(block.records) } : block)
}

type Message = Snapshot['messages'][number]
export type ConversationEntry =
  | { kind: 'message'; message: Message }
  | { kind: 'run'; run: Snapshot['runs'][number]; blocks: RunBlock[] }

function matchesMessage(message: Message, blocks: RunBlock[]): boolean {
  const text = contentText(message.data.content)
  return message.type === 'ai' && blocks.some(block => block.kind === 'text'
    && (block.messageId && block.messageId === message.data.id || block.text.trim() === text.trim()))
}

/** 有顺序记录的运行按用户消息定位；旧记录沿用最终答复前的工具列表。 */
export function savedConversation(snapshot: Snapshot | null): ConversationEntry[] {
  if (!snapshot) return []
  const liveBlocks = runTimeline(snapshot.active_run?.records ?? [])
  const messages = snapshot.messages.filter(message => ['human', 'ai'].includes(message.type) && contentText(message.data.content))
  const owners: Array<string | undefined> = []
  const humanIndexes = new Map<string, number>()
  const lastIndexes = new Map<string, number>()
  let owner: string | undefined
  messages.forEach((message, index) => {
    if (message.type === 'human') {
      owner = typeof message.data.id === 'string' ? message.data.id : undefined
      if (owner) humanIndexes.set(owner, index)
    }
    owners.push(owner)
    if (owner && ['human', 'ai'].includes(message.type)) lastIndexes.set(owner, index)
  })
  const hidden = new Set<number>()
  const before = new Map<number, ConversationEntry[]>()
  const after = new Map<number, ConversationEntry[]>()
  const unassigned: ConversationEntry[] = []
  for (const run of snapshot.runs) {
    const userId = run.user_message_id
    const humanIndex = userId ? humanIndexes.get(userId) : undefined
    const blocks = runTimeline(run.records ?? [], run.tools as Record<string, RunRecord>)
    // shortcut: 旧记录缺少正文顺序或用户归属时保留工具列表，原始记录补齐后再还原分段。
    const narrated = humanIndex !== undefined && blocks.some(block => block.kind === 'text')
    const entry: ConversationEntry = { kind: 'run', run, blocks: narrated ? blocks : [] }
    if (narrated) {
      messages.forEach((message, index) => {
        if (owners[index] === userId && matchesMessage(message, blocks)) hidden.add(index)
      })
    }
    const index = narrated ? humanIndex : userId ? lastIndexes.get(userId) : undefined
    if (index === undefined) { unassigned.push(entry); continue }
    const placements = narrated || messages[index].type === 'human' ? after : before
    placements.set(index, [...(placements.get(index) ?? []), entry])
  }
  const entries: ConversationEntry[] = []
  messages.forEach((message, index) => {
    entries.push(...(before.get(index) ?? []))
    const liveDuplicate = snapshot.active_run?.user_message_id && owners[index] === snapshot.active_run.user_message_id && matchesMessage(message, liveBlocks)
    if (!hidden.has(index) && !liveDuplicate && ['human', 'ai'].includes(message.type)) entries.push({ kind: 'message', message })
    entries.push(...(after.get(index) ?? []))
  })
  return [...entries, ...unassigned]
}
