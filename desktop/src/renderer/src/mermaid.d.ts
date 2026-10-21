declare module 'mermaid' {
  export interface MermaidConfig {
    startOnLoad?: boolean
    theme?: string
    securityLevel?: 'strict' | 'loose' | 'antiscript' | 'sandbox'
    fontFamily?: string
    [key: string]: unknown
  }

  export interface RenderResult {
    svg: string
    bindFunctions?: (element: Element) => void
  }

  interface Mermaid {
    initialize(config: MermaidConfig): void
    parse(text: string, parseOptions?: { suppressErrors?: boolean }): Promise<boolean | unknown>
    render(id: string, text: string, container?: Element): Promise<RenderResult>
    [key: string]: unknown
  }

  const mermaid: Mermaid
  export default mermaid
}
