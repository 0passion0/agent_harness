export function contentText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content.map(block => typeof block === 'object' && block !== null && 'text' in block && typeof block.text === 'string' ? block.text : '').join('\n')
  }
  return JSON.stringify(content) ?? ''
}
