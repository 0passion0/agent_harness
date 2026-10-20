const { app, BrowserWindow } = require('electron')
const { writeFileSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
app.setPath('userData', process.env.DESKTOP_SMOKE_DIR)
const preload = path.join(process.env.DESKTOP_SMOKE_DIR, 'preload.cjs')
writeFileSync(preload, `
const { contextBridge } = require('electron')
const listeners = new Set()
const snapshot = { session_id:'chat',thread_id:'chat',thread_ids:['chat'],messages:[],attachments:[],artifacts:[],runs:[],totals:{},unsettled_history:false }
const application = { build_mode:'development',projects:[{id:'project',name:'测试项目',path:'C:/workspace'}],active_project_id:'project',model:{provider:'deepseek',model:'test'},runtimes:{python:'',node:''},permission_mode:'ask',data_dir:'data' }
contextBridge.exposeInMainWorld('medpeer', {
  onEvent(fn) { listeners.add(fn); return () => listeners.delete(fn) },
  async command(command) {
    switch(command.method) {
      case 'app.snapshot': return application
      case 'session.snapshot': case 'protocol.hello': return snapshot
      case 'session.list': return [{id:'chat',title:'测试任务',updated:'2026-10-20'}]
      case 'config.inspect': return {agent:{content:'',source:''},overrides:{agent:''}}
      case 'run.start':
        setTimeout(() => {
          snapshot.messages = [{type:'human',data:{content:command.params.text}},{type:'ai',data:{content:'已完成验证'}}]
          for(const fn of listeners) fn({event:'run.finished',session_id:'chat',run_id:command.id,data:{status:'complete',snapshot}})
        }, 50)
        return {run_id:command.id}
      default: throw new Error('Unexpected command: '+command.method)
    }
  },
  async listWorkspaceDirectory() { return [] }
})
`)
const deadline = setTimeout(() => { console.error('Renderer smoke timed out'); app.exit(1) }, 20000)
app.whenReady().then(async () => {
  const window = new BrowserWindow({ show:false, webPreferences:{preload,contextIsolation:true} })
  const url = process.env.ELECTRON_RENDERER_URL
  if(url) await window.loadURL(url)
  else await window.loadFile(path.join(__dirname,'../out/renderer/index.html'))
  const result = await window.webContents.executeJavaScript(`(async () => {
    const until = async predicate => { for(let i=0;i<100;i++){ if(predicate()) return; await new Promise(r=>setTimeout(r,40)) }; throw new Error('UI condition timed out') }
    await until(() => document.querySelector('[aria-label="任务输入"]') && !document.querySelector('[aria-label="任务输入"]').disabled)
    if(document.body.innerText.includes('MCP 服务') || document.body.innerText.includes('插件与权限')) throw new Error('Removed extension UI is still present')
    const input = document.querySelector('[aria-label="任务输入"]')
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'验证文件工具')
    input.dispatchEvent(new Event('input',{bubbles:true}))
    await until(() => !document.querySelector('[aria-label="提交任务"]').disabled)
    document.querySelector('[aria-label="提交任务"]').click()
    await until(() => document.body.innerText.includes('已完成验证'))
    return true
  })()`)
  assert.equal(result,true)
  console.log('Electron smoke passed: renderer startup, current protocol and completed conversation')
  clearTimeout(deadline)
  window.destroy()
  app.quit()
}).catch(error => { console.error(error); clearTimeout(deadline); app.exit(1) })
