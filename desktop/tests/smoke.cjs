const { app, BrowserWindow } = require('electron')
const { mkdirSync, writeFileSync } = require('node:fs')
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
      case 'session.search': case 'session.list': return [{id:'chat',title:'测试任务',updated:'2026-10-20'}]
      case 'config.inspect': return {agent:{content:'',source:''},overrides:{agent:''}}
      case 'harness.projects': return projects
      case 'usage.stats': return {today:'2026-10-22',tracking_since:null,dates:[],model_names:[],models:[],daily:[],totals:{total_tokens:0,input_tokens:0,output_tokens:0,requests:0,reported_requests:0,cache_read:0,reasoning:0,jev_requests:0}}
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
          snapshot.messages = [
            {type:'human',data:{id:'u1',content:command.params.text}},
            {type:'ai',data:{content:'明白，我会细化按钮、下拉框和表单对齐，重点检查页面的一致性。保存按钮会使用浅色强调底，减少大面积高饱和填色；代码上会把设置页样式收回组件目录，并补齐表单标签和键盘焦点状态。'}},
            {type:'ai',data:{content:['已完成验证，保留原有色系，减少高饱和填色。','','- 统一按钮、下拉框、输入框的尺寸、圆角和交互状态。','- 调整导航、卡片分组与留白，宽屏双列、窄屏单列。','- 整理组件样式与导航定义，补齐表单标签和键盘焦点。','','类型检查、6 项测试、构建及三种窗口宽度的 Electron 检查均通过。'].join(String.fromCharCode(10))}}
          ]
          snapshot.runs = [{id:command.id,user_message_id:'u1',status:'complete',elapsed:385,tools:{},requests:[],records:[
            {kind:'tool_end',call_id:'edit-1',name:'edit_file',arguments:{path:'src/App.tsx'},status:'success',output:'Updated'},
            {kind:'tool_end',call_id:'cmd-1',name:'shell_exec',arguments:{command:'npm run typecheck'},status:'success',output:'Typecheck passed'},
            {kind:'tool_end',call_id:'cmd-2',name:'shell_exec',arguments:{command:'npx vitest run tests/ui/harness-settings.test.tsx tests/ui/usage-settings.test.tsx'},status:'success',output:'6 tests passed'},
            {kind:'tool_end',call_id:'edit-2',name:'edit_file',arguments:{path:'src/plugins/user_config/index.tsx'},status:'success',output:'Updated'}
          ]}]
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
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'按钮下拉框，布局的规范性完整性代码的组织优美，嵌套的合理性；不要饱和度轰炸')
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
  mkdirSync(path.join(__dirname, '../test-artifacts'), { recursive: true })
  for (const width of [1280, 920, 640]) {
    window.setContentSize(width, 900)
    for (const title of ['用户指令', '技能', '执行权限', 'Token 消耗']) {
      await window.webContents.executeJavaScript(`(async () => {
        [...document.querySelectorAll('.settings-nav-item')].find(element => element.textContent.startsWith('${title}')).click()
        await new Promise(resolve => setTimeout(resolve, 80))
        const content = document.querySelector('.settings-content-scroll')
        if(content.scrollWidth > content.clientWidth + 1) throw new Error('${title} overflows at ${width}px')
        for(const control of content.querySelectorAll('input, select, textarea')) {
          const bounds = control.getBoundingClientRect()
          const card = control.closest('.pref-section').getBoundingClientRect()
          if(bounds.left < card.left || bounds.right > card.right) throw new Error('${title} control escapes its card at ${width}px')
        }
      })()`)
    }
    await window.webContents.executeJavaScript(`(async () => {
      [...document.querySelectorAll('.settings-nav-item')].find(element => element.textContent.startsWith('模型与运行环境')).click()
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      const cards = [...document.querySelectorAll('.settings-model-grid > .pref-section')].map(element => element.getBoundingClientRect())
      if(cards.length !== 3) throw new Error('Missing model settings cards')
      if(${width} === 1280 && document.querySelector('.settings-navigation').getBoundingClientRect().width !== 224) throw new Error('Settings navigation width was overridden')
      if(${width} === 1280 && Math.abs(cards[0].top - cards[1].top) > 1) throw new Error('Wide settings should have two columns')
      if(${width} < 1000 && cards[1].top <= cards[0].top) throw new Error('Narrow settings should stack cards')
      const content = document.querySelector('.settings-content-scroll')
      if(content.scrollWidth > content.clientWidth + 1) throw new Error('Settings content overflows horizontally')
      const input = document.querySelector('.pref-form input').getBoundingClientRect()
      if(input.right > cards[0].right || input.width < cards[0].width - 55) throw new Error('Settings input does not fill its card')
      const active = document.querySelector('.settings-nav-item[aria-current="page"]')
      active.focus()
      if(document.activeElement !== active) throw new Error('Settings navigation is not keyboard focusable')
    })()`)
    writeFileSync(path.join(__dirname, '../test-artifacts', `settings-${width}.png`), (await window.webContents.capturePage()).toPNG())
    if (width === 1280) {
      await window.webContents.executeJavaScript(`(async () => {
        document.querySelector('.product-v3').classList.add('dark')
        await new Promise(resolve => setTimeout(resolve, 80))
        if(getComputedStyle(document.querySelector('.pref-section')).backgroundColor !== 'rgb(27, 28, 50)') throw new Error('Dark settings palette was overridden')
      })()`)
      writeFileSync(path.join(__dirname, '../test-artifacts/settings-dark.png'), (await window.webContents.capturePage()).toPNG())
      await window.webContents.executeJavaScript(`document.querySelector('.product-v3').classList.remove('dark')`)
    }
  }
  await window.webContents.executeJavaScript(`(async () => {
    [...document.querySelectorAll('button')].find(element => element.textContent.trim() === '返回任务').click()
    await new Promise(resolve => setTimeout(resolve, 80))
    document.querySelector('[aria-label="切换侧边栏"]').click()
    document.querySelector('.inline-activity-card').open = true
  })()`)
  for (const width of [1000, 640]) {
    window.setContentSize(width, 1040)
    await window.webContents.executeJavaScript(`(async () => {
      await new Promise(resolve => setTimeout(resolve, 100))
      const pane = document.querySelector('.conversation')
      pane.scrollTop = 0
      const assistant = document.querySelector('.message.ai')
      if(getComputedStyle(assistant).paddingLeft !== '0px') throw new Error('Assistant transcript is indented')
      if(document.querySelector('.message-avatar')) throw new Error('Assistant avatar is still present')
      if(pane.scrollWidth > pane.clientWidth + 1) throw new Error('Transcript overflows at ${width}px')
      const row = document.querySelector('.act-item')
      row.open = true
      await new Promise(resolve => setTimeout(resolve, 40))
      if(!row.textContent.includes('Updated')) throw new Error('Tool output is unavailable after expanding')
      row.open = false
      const bubble = document.querySelector('.message.human').getBoundingClientRect()
      const reply = assistant.getBoundingClientRect()
      if(bubble.left <= reply.left || Math.abs(bubble.right - reply.right) > 1) throw new Error('User message is not aligned to the right')
    })()`)
    writeFileSync(path.join(__dirname, '../test-artifacts', `codex-conversation-${width}.png`), (await window.webContents.capturePage()).toPNG())
  }
  console.log('Electron smoke passed: conversation, real project choices, isolated instructions and skill import/edit/delete')
  clearTimeout(deadline)
  window.destroy()
  app.quit()
}).catch(error => { console.error(error); clearTimeout(deadline); app.exit(1) })
