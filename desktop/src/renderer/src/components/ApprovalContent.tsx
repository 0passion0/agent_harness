import type { Json } from '../../../shared/protocol'

export function ReadableValue({ value }: { value: Json }) {
  return <pre className="approval-text">{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>
}

export function ApprovalContent({ requests }: { requests: Array<{ name: string; args: Json }> }) {
  return <div className="approval-operations">{requests.map((request, index) => <article className="approval-operation" key={index}>
    <strong>{request.name}</strong><ReadableValue value={request.args} />
  </article>)}</div>
}
