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
const projects = [{id:'project',name:'workspace',path:'C:/workspace'},{id:'other',name:'another-app',path:'D:/projects/another-app'}]
const resources = Object.fromEntries(['', ...projects.map(project => project.id)].map(id => {
  const root = id ? projects.find(project => project.id === id).path : 'C:/Users/test/.agent-harness'
  return [id, {scope:id ? 'project' : 'global',root,agent:{content:id || 'system rules',source:root+'/AGENTS.md',exists:true},skills:[]}]
}))
contextBridge.exposeInMainWorld('medpeer', {
  onEvent(fn) { listeners.add(fn); return () => listeners.delete(fn) },
  async command(command) {
    switch(command.method) {
      case 'app.snapshot': return application
      case 'session.snapshot': case 'protocol.hello': return snapshot
      case 'session.list': return [{id:'chat',title:'测试任务',updated:'2026-10-20'}]
      case 'config.inspect': return {agent:{content:'',source:''},overrides:{agent:''}}
      case 'harness.projects': return projects
      case 'harness.inspect': return resources[command.params.project_id]
      case 'harness.save': case 'harness.remove': {
        const {project_id,kind,name,content} = command.params
        const config = resources[project_id]
        if(kind === 'agent') config.agent = {...config.agent,content:content ?? '',exists:command.method === 'harness.save'}
        else if(command.method === 'harness.save') config.skills.find(skill => skill.name === name).content = content
        else config.skills = config.skills.filter(skill => skill.name !== name)
        return config
      }
      case 'run.start':
        setTimeout(() => {
          snapshot.messages = [{type:'human',data:{content:command.params.text}},{type:'ai',data:{content:'已完成验证'}}]
          for(const fn of listeners) fn({event:'run.finished',session_id:'chat',run_id:command.id,data:{status:'complete',snapshot}})
        }, 50)
        return {run_id:command.id}
      default: throw new Error('Unexpected command: '+command.method)
    }
  },
  async importSkills(scope, projectId) {
    const config = resources[projectId]
    config.skills.push({name:'zip-skill',description:'Imported skill',directory:config.root+'/skills/zip-skill',content:'Imported content'})
    return config
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
    const button = text => [...document.querySelectorAll('button')].find(element => element.textContent.trim() === text)
    const setValue = (element, value) => {
      Object.getOwnPropertyDescriptor(element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLTextAreaElement.prototype,'value').set.call(element,value)
      element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input',{bubbles:true}))
    }
    document.querySelector('button[aria-label="设置"]').click()
    await until(() => document.querySelector('.settings-nav-item'))
    const category = text => [...document.querySelectorAll('.settings-nav-item')].find(element => element.textContent.startsWith(text)).click()
    category('用户指令')
    const editor = () => document.querySelector('[aria-label="Agent 指令"]')
    const scope = () => document.querySelector('.pref-toolbar select')
    await until(() => editor() && !editor().disabled)
    if([...scope().options].map(option => option.text).join('|') !== '系统 ~/.agent-harness|workspace — C:/workspace|another-app — D:/projects/another-app') throw new Error('Project directory labels are incorrect')
    setValue(scope(),'other')
    await until(() => editor().value === 'other' && !editor().disabled)
    setValue(editor(),'other edited')
    button('保存指令').click()
    await until(() => document.body.innerText.includes('已保存') && !editor().disabled)
    setValue(scope(),'')
    await until(() => editor().value === 'system rules' && !editor().disabled)
    setValue(scope(),'project')
    await until(() => editor().value === 'project' && !editor().disabled)
    setValue(scope(),'other')
    await until(() => editor().value === 'other edited' && !editor().disabled)
    button('删除指令文件').click()
    await until(() => editor().value === '' && button('删除指令文件').disabled)
    category('技能')
    await until(() => button('导入 ZIP 技能') && !button('导入 ZIP 技能').disabled)
    button('导入 ZIP 技能').click()
    await until(() => document.body.innerText.includes('zip-skill') && !button('编辑').disabled)
    button('编辑').click()
    await until(() => document.querySelector('.skill-editor-box textarea'))
    setValue(document.querySelector('.skill-editor-box textarea'),'Updated skill')
    button('保存技能').click()
    await until(() => !document.querySelector('.skill-editor-box') && !button('编辑').disabled)
    button('编辑').click()
    await until(() => document.querySelector('.skill-editor-box textarea'))
    if(document.querySelector('.skill-editor-box textarea').value !== 'Updated skill') throw new Error('Skill edit was not persisted')
    setValue(scope(),'other')
    await until(() => document.body.innerText.includes('此范围还没有技能。') && !scope().disabled)
    setValue(scope(),'')
    await until(() => document.body.innerText.includes('zip-skill') && !button('删除').disabled)
    button('删除').click()
    await until(() => document.body.innerText.includes('此范围还没有技能。'))
    return true
  })()`)
  assert.equal(result,true)
  console.log('Electron smoke passed: conversation, real project choices, isolated instructions and skill import/edit/delete')
  clearTimeout(deadline)
  window.destroy()
  app.quit()
}).catch(error => { console.error(error); clearTimeout(deadline); app.exit(1) })
