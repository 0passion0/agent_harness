const { spawn } = require('node:child_process')
const { once } = require('node:events')
const { mkdtempSync, rmSync } = require('node:fs')
const { tmpdir } = require('node:os')
const path = require('node:path')
const assert = require('node:assert/strict')

async function smoke(rendererUrl) {
  const temporary = mkdtempSync(path.join(tmpdir(), 'harness-desktop-smoke-'))
  const env = { ...process.env, DESKTOP_SMOKE_DIR: temporary }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.ELECTRON_RENDERER_URL
  if (rendererUrl) env.ELECTRON_RENDERER_URL = rendererUrl
  try {
    const child = spawn(require('electron'), [path.join(__dirname, 'smoke.cjs')], {
      env, stdio: 'inherit', windowsHide: true, timeout: 30000
    })
    const [status] = await once(child, 'exit')
    assert.equal(status, 0, `Desktop smoke failed (${rendererUrl ?? 'file'})`)
  } finally {
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(tmpdir()))
    rmSync(temporary, { recursive: true, force: true })
  }
}

async function main() {
  const { createServer } = await import('vite')
  const server = await createServer({ configFile: false, root: path.resolve(__dirname, '../out/renderer'), server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  try {
    // electron-vite supplies the renderer URL without a trailing slash.
    await smoke(`http://127.0.0.1:${server.httpServer.address().port}`)
    await smoke()
  } finally {
    await server.close()
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
