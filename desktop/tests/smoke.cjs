const { app, BrowserWindow, ipcMain } = require('electron')
const { mkdirSync, writeFileSync, readFileSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
app.setPath('userData', process.env.DESKTOP_SMOKE_DIR)
const preload = path.join(process.env.DESKTOP_SMOKE_DIR, 'preload.cjs')
writeFileSync(preload, `
const { contextBridge, ipcRenderer } = require('electron')
const listeners = new Set()
const tick = String.fromCharCode(96)
const snapshot = { session_id:'chat',thread_id:'chat',thread_ids:['chat'],messages:[],attachments:[],artifacts:[],runs:[],totals:{},unsettled_history:false }
const application = { build_mode:'development',projects:[{id:'project',name:'测试项目',path:'C:/workspace'}],active_project_id:'project',model:{provider:'deepseek',model:'test'},runtimes:{python:'',node:''},permission_mode:'ask',data_dir:'data' }
const projects = [{id:'project',name:'workspace',path:'C:/workspace'},{id:'other',name:'another-app',path:'D:/projects/another-app'}]
const resources = Object.fromEntries(['', ...projects.map(project => project.id)].map(id => {
  const root = id ? projects.find(project => project.id === id).path : 'C:/Users/test/.agent-harness'
  return [id, {scope:id ? 'project' : 'global',root,agent:{content:id || 'system rules',source:root+'/AGENTS.md',exists:true},skills:[]}]
}))
resources.project.skills = [
  {name:'review',description:'审查研究结果与证据',directory:'C:/workspace/.agents/skills/review',content:'Review guidance'},
  {name:'zip-skill',description:'Project override',directory:'C:/workspace/.agents/skills/zip-skill',content:'Project guidance'}
]
contextBridge.exposeInMainWorld('medpeer', {
  setWindowTheme: theme => ipcRenderer.invoke('window:theme', theme),
  onEvent(fn) { listeners.add(fn); return () => listeners.delete(fn) },
  async command(command) {
    switch(command.method) {
      case 'app.snapshot': return application
      case 'session.snapshot': case 'protocol.hello': return snapshot
      case 'session.clear':
        snapshot.messages = []; snapshot.runs = []; snapshot.artifacts = []
        return snapshot
      case 'session.search': case 'session.list': return [{id:'chat',title:'测试任务',updated:'2026-10-20'}]
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
        if(command.params.text === '验证分段流式') {
          const texts = [
            '我会先检查流式事件与对话组件，确认文本和工具记录在哪里失去顺序。',
            '问题已经定位：文本与工具被分别聚合。接下来按每段 AI 进度说明切分，让工具跟随对应步骤。',
            '分段展示已完成。现在验证实时追加、历史回放和中断恢复，并检查窄窗口及深色主题。',
            '验证结束。对话将按“进度说明 → 本阶段工具 → 下一段说明”的顺序展示，工具参数和结果仍可展开查看。'
          ]
          const records = []
          const emit = data => {
            const record = {...data,seq:records.length + 1}
            records.push(record)
            for(const fn of listeners) fn({event:'run.event',session_id:'chat',run_id:command.id,data:record})
          }
          const narrate = index => {
            emit({kind:'model_start',operation_id:'stage-'+index})
            for(const text of [texts[index].slice(0, 10),texts[index].slice(10)]) emit({kind:'text',operation_id:'stage-'+index,message_id:'stage-message-'+index,text})
            emit({kind:'model_end',operation_id:'stage-'+index,message_id:'stage-message-'+index,text:texts[index]})
          }
          const tool = (id, name, args) => {
            emit({kind:'tool_start',call_id:id,name,arguments:args,status:'running'})
            emit({kind:'tool_end',call_id:id,name,status:'success',output:'检查完成'})
          }
          setTimeout(() => {
            narrate(0)
            tool('stage-read-1','read_file',{path:'src/components/Conversation.tsx'})
            tool('stage-read-2','read_file',{path:'src/state/agent-state.ts'})
          }, 40)
          setTimeout(() => {
            narrate(1)
            tool('stage-edit','edit_file',{path:'src/components/run-timeline.ts'})
            emit({kind:'tool_start',call_id:'stage-check',name:'shell_exec',arguments:{command:'npm run typecheck'},status:'running'})
          }, 500)
          setTimeout(() => {
            emit({kind:'tool_end',call_id:'stage-check',name:'shell_exec',status:'success',output:'Typecheck passed'})
            narrate(2)
            tool('stage-tests','shell_exec',{command:'npx vitest run tests/ui'})
            tool('stage-smoke','shell_exec',{command:'npm run test:smoke'})
            narrate(3)
            snapshot.messages.push({type:'human',data:{id:'staged-user',content:command.params.text}},...texts.map((text,index) => ({type:'ai',data:{id:'stage-message-'+index,content:text}})))
            snapshot.runs.push({id:command.id,user_message_id:'staged-user',status:'complete',elapsed:83,tools:{},requests:[],records})
            for(const fn of listeners) fn({event:'run.finished',session_id:'chat',run_id:command.id,data:{status:'complete',snapshot}})
          }, 1200)
          return {run_id:command.id}
        }
        if(command.params.text === '验证图表') {
          const fence = tick.repeat(3), newline = String.fromCharCode(10)
          const text = [fence+'svg','<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 80"><rect width="160" height="80" fill="#e8f3ff"/><circle cx="80" cy="40" r="24" fill="#6158db"/></svg>',fence,'',fence+'MERMAID','graph TD','A[开始] --> B[完成]',fence,'',fence+'typescript','const answer: number = 42',fence].join(newline)
          const split = text.indexOf('<') + 1
          const emit = chunk => { for(const fn of listeners) fn({event:'run.event',session_id:'chat',run_id:command.id,data:{kind:'text',text:chunk}}) }
          setTimeout(() => emit(text.slice(0, split)), 50)
          setTimeout(() => emit(text.slice(split)), 350)
          setTimeout(() => {
            snapshot.messages.push({type:'human',data:{id:'u2',content:command.params.text}},{type:'ai',data:{content:text}})
            snapshot.runs.push({id:command.id,user_message_id:'u2',status:'complete',elapsed:1,tools:{},requests:[],records:[]})
            for(const fn of listeners) fn({event:'run.finished',session_id:'chat',run_id:command.id,data:{status:'complete',snapshot}})
          }, 1400)
          return {run_id:command.id}
        }
        setTimeout(() => {
          snapshot.messages = [
            {type:'human',data:{id:'u1',content:command.params.text}},
            {type:'ai',data:{content:'明白，我会细化按钮、下拉框和表单对齐，重点检查页面的一致性。保存按钮会使用浅色强调底，减少大面积高饱和填色；代码上会把设置页样式收回组件目录，并补齐表单标签和键盘焦点状态。'}},
            {type:'ai',data:{content:['已完成验证，保留原有色系，减少高饱和填色。','','- 统一按钮、下拉框、输入框的尺寸、圆角和交互状态。','- 调整导航、卡片分组与留白，宽屏双列、窄屏单列。','- 整理组件样式与导航定义，补齐表单标签和键盘焦点。','','目录 '+tick+'desktop/'+tick+'，文件 '+tick+'src/App.tsx'+tick+'，函数 '+tick+'create_agent'+tick+' 保持为普通文字。','','类型检查、6 项测试、构建及三种窗口宽度的 Electron 检查均通过。'].join(String.fromCharCode(10))}}
          ]
          snapshot.artifacts = [
            {id:'old-file',workspace_id:'project',name:'旧报告.pdf',relative_path:'exports/old.pdf',media_type:'application/pdf',source_run_id:'old'},
            {id:'new-file',workspace_id:'project',name:'本轮报告.pdf',relative_path:'exports/new.pdf',media_type:'application/pdf',source_run_id:command.id}
          ]
          snapshot.runs = [{id:command.id,user_message_id:'u1',status:'complete',elapsed:385,tools:{},requests:[],records:[
            {kind:'tool_end',call_id:'edit-1',name:'edit_file',arguments:{path:'src/App.tsx'},status:'success',output:'Updated'},
            {kind:'tool_end',call_id:'cmd-1',name:'shell_exec',arguments:{command:'npm run typecheck'},status:'success',output:JSON.stringify({output:'Typecheck passed',exit_code:0,timed_out:false,output_path:'/logs/typecheck.log'})},
            {kind:'tool_end',call_id:'cmd-2',name:'shell_exec',arguments:{command:'npx vitest run tests/ui/harness-settings.test.tsx tests/ui/usage-settings.test.tsx'},status:'success',output:'6 tests passed'},
            {kind:'tool_end',call_id:'edit-2',name:'edit_file',arguments:{path:'src/plugins/user_config/index.tsx'},status:'success',output:'Updated'},
            {kind:'tool_end',call_id:'skill-1',name:'read_file',arguments:{file_path:'/.agents/skills/review/SKILL.md'},status:'success',output:'Review guidance'}
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
  const window = new BrowserWindow({ show:false, titleBarStyle:'hidden', titleBarOverlay:{color:'#f3f3fb',symbolColor:'#3a3c54',height:36}, webPreferences:{preload,contextIsolation:true,backgroundThrottling:false} })
  const themes = []
  ipcMain.handle('window:theme', (_event, theme) => {
    themes.push(theme)
    window.setTitleBarOverlay({color:theme === 'dark' ? '#191a2f' : '#f3f3fb',symbolColor:theme === 'dark' ? '#d4d1eb' : '#3a3c54',height:36})
  })
  const url = process.env.ELECTRON_RENDERER_URL
  if(url) await window.loadURL(url)
  else await window.loadFile(path.join(__dirname,'../out/renderer/index.html'))
  const result = await window.webContents.executeJavaScript(`(async () => {
    const until = async predicate => { for(let i=0;i<100;i++){ if(predicate()) return; await new Promise(r=>setTimeout(r,40)) }; throw new Error('UI condition timed out') }
    await until(() => document.querySelector('[aria-label="任务输入"]') && !document.querySelector('[aria-label="任务输入"]').disabled)
    const titlebar = document.querySelector('.window-titlebar')
    if(!titlebar || getComputedStyle(titlebar).webkitAppRegion !== 'drag') throw new Error('Window titlebar is not draggable')
    const workspace = document.querySelector('.workspace-layout')
    if(workspace.getBoundingClientRect().top !== titlebar.getBoundingClientRect().bottom) throw new Error('Window titlebar overlaps workspace')
    const surface = document.querySelector('.workspace-surface')
    const rail = document.querySelector('.app-rail')
    if(getComputedStyle(surface).borderTopLeftRadius !== '14px') throw new Error('Window workspace lost its rounded corners')
    if(getComputedStyle(workspace).borderTopLeftRadius !== '0px' || getComputedStyle(rail).borderRightWidth !== '0px') throw new Error('Rail is still inside the rounded panel')
    if(surface.getBoundingClientRect().left !== rail.getBoundingClientRect().right) throw new Error('Rounded panel must start beside the rail')
    if(workspace.getBoundingClientRect().bottom > innerHeight) throw new Error('Window chrome pushes workspace below viewport')
    const themeButton = () => document.querySelector('.sidebar-footer button:last-child')
    themeButton().click()
    await until(() => document.querySelector('.product-v3.dark'))
    themeButton().click()
    await until(() => !document.querySelector('.product-v3.dark'))
    await document.fonts.ready
    if(![...document.fonts].some(font => font.family.includes('JetBrains Mono') && font.status === 'loaded')) throw new Error('Local JetBrains Mono font did not load')
    for(const element of document.querySelectorAll('.app, .app button, .app input, .app textarea, .app select')) {
      if(!getComputedStyle(element).fontFamily.startsWith('"JetBrains Mono"')) throw new Error('UI control does not use JetBrains Mono')
    }
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
  assert.ok(themes.includes('light') && themes.includes('dark'), 'Native window controls must follow theme changes')
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
      await new Promise(resolve => setTimeout(resolve, 100))
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
  window.setContentSize(1280, 900)
  await window.webContents.executeJavaScript(`(async () => {
    [...document.querySelectorAll('button')].find(element => element.textContent.trim() === '返回任务').click()
    await new Promise(resolve => setTimeout(resolve, 80))
  })()`)
  for (const theme of ['light', 'dark']) {
    await window.webContents.executeJavaScript(`(async () => {
      if(document.querySelector('.product-v3').classList.contains('dark') !== ${theme === 'dark'}) document.querySelector('.sidebar-footer button:last-child').click()
      await new Promise(resolve => setTimeout(resolve, 80))
      const surface = document.querySelector('.workspace-surface')
      if(document.querySelector('.sidebar').parentElement !== surface || document.querySelector('.task-main').parentElement !== surface) throw new Error('Sidebar and task must share the rounded panel')
      if(getComputedStyle(document.querySelector('.app-rail')).backgroundColor !== 'rgba(0, 0, 0, 0)') throw new Error('Rail must blend into the window chrome')
    })()`)
    writeFileSync(path.join(__dirname, '../test-artifacts', `window-chrome-${theme}.png`), (await window.webContents.capturePage()).toPNG())
  }
  await window.webContents.executeJavaScript(`(async () => {
    document.querySelector('.sidebar-footer button:last-child').click()
    await new Promise(resolve => setTimeout(resolve, 80))
    document.querySelector('[aria-label="切换侧边栏"]').click()
    document.querySelector('.inline-activity-card').open = true
  })()`)
  await window.webContents.executeJavaScript(`(async () => {
    const until = async predicate => { for(let i=0;i<100;i++){ if(predicate()) return; await new Promise(resolve=>setTimeout(resolve,30)) }; throw new Error('Skill menu condition timed out') }
    await window.medpeer.importSkills('global', '')
    const input = document.querySelector('[aria-label="任务输入"]')
    const setValue = (element, value) => {
      Object.getOwnPropertyDescriptor(element instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype,'value').set.call(element,value)
      element.dispatchEvent(new Event('input',{bubbles:true}))
    }
    setValue(input, '/')
    await until(() => document.querySelector('[aria-label="搜索技能"]'))
    await until(() => document.querySelectorAll('[role="option"]').length === 2)
    if(document.activeElement.getAttribute('aria-label') !== '搜索技能') throw new Error('Slash did not focus skill search')
    if(!document.querySelector('.skill-menu').textContent.includes('Project override')) throw new Error('Project skill did not override system skill')
    const search = document.querySelector('[aria-label="搜索技能"]')
    setValue(search, '不存在')
    await until(() => document.querySelector('.skill-menu').textContent.includes('没有找到匹配的技能'))
    search.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}))
    setValue(search, '证据')
    await until(() => document.querySelectorAll('[role="option"]').length === 1)
    search.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))
    await until(() => input.value === '$review ' && !document.querySelector('.skill-menu'))
    await until(() => document.activeElement === input)
    document.querySelector('[aria-label="选择技能"]').click()
    await until(() => document.querySelector('[aria-label="搜索技能"]'))
    document.querySelector('[aria-label="搜索技能"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))
    await until(() => !document.querySelector('.skill-menu'))
    if(input.value !== '$review ') throw new Error('Closing the skill menu changed the draft')
    if(!document.querySelector('.is-skill').textContent.includes('已读取技能 review')) throw new Error('Actual skill activation is not visible')
    setValue(input, '')
  })()`)
  for (const width of [1000, 640]) {
    window.setContentSize(width, 1040)
    await window.webContents.executeJavaScript(`(async () => {
      await new Promise(resolve => setTimeout(resolve, 100))
      const pane = document.querySelector('.conversation')
      for(const element of pane.querySelectorAll('.message-content, .act-item-title, .act-json-pre, code')) {
        if(!getComputedStyle(element).fontFamily.startsWith('"JetBrains Mono"')) throw new Error('Transcript font was overridden')
      }
      pane.scrollTop = 0
      const inline = pane.querySelector('.modern-inline-code')
      if(!inline || getComputedStyle(inline).backgroundColor !== 'rgba(0, 0, 0, 0)' || getComputedStyle(inline).borderTopWidth !== '0px') throw new Error('Inline code still has a colored badge')
      if(pane.querySelector('.message-content .file-link-chip')) throw new Error('Inline paths became file widgets')
      if(!pane.textContent.includes('本轮报告.pdf') || pane.textContent.includes('旧报告.pdf')) throw new Error('Transcript shows stale deliveries')
      const assistant = document.querySelector('.message.ai')
      if(getComputedStyle(assistant).paddingLeft !== '0px') throw new Error('Assistant transcript is indented')
      if(document.querySelector('.message-avatar')) throw new Error('Assistant avatar is still present')
      if(pane.scrollWidth > pane.clientWidth + 1) throw new Error('Transcript overflows at ${width}px')
      const composer = document.querySelector('.task-composer')
      if(composer.scrollWidth > composer.clientWidth + 1) throw new Error('Composer overflows at ${width}px')
      const app = document.querySelector('.product-v3')
      for(const dark of [false, true]) {
        app.classList.toggle('dark', dark)
        const transcript = getComputedStyle(document.querySelector('.codex-conversation'))
        const theme = getComputedStyle(app)
        const probe = document.createElement('span')
        probe.style.color = theme.getPropertyValue('--text')
        app.appendChild(probe)
        if(transcript.color !== getComputedStyle(probe).color) throw new Error('Transcript changed the theme palette')
        probe.remove()
      }
      app.classList.remove('dark')
      const row = document.querySelector('.act-item')
      row.open = true
      await new Promise(resolve => setTimeout(resolve, 40))
      if(!row.textContent.includes('Updated')) throw new Error('Tool output is unavailable after expanding')
      row.open = false
      const terminal = [...document.querySelectorAll('.act-item')].find(element => element.textContent.includes('已运行 npm run typecheck'))
      terminal.open = true
      await new Promise(resolve => setTimeout(resolve, 40))
      if(!terminal.querySelector('.act-terminal pre').textContent.includes('Typecheck passed') || !terminal.querySelector('.act-terminal pre').textContent.includes('退出码 0')) throw new Error('Terminal result is not readable')
      if(!terminal.querySelector('.act-result-metadata').textContent.includes('/logs/typecheck.log')) throw new Error('Terminal metadata was lost')
      for(const selector of ['.inline-activity-bar', '.act-item-header', '.act-payload-box']) {
        const style = getComputedStyle(document.querySelector(selector))
        if(style.backgroundColor !== 'rgba(0, 0, 0, 0)' || style.borderTopWidth !== '0px') throw new Error('Tool activity still has a background or panel border')
      }
      const bubble = document.querySelector('.message.human').getBoundingClientRect()
      const reply = assistant.getBoundingClientRect()
      if(bubble.left <= reply.left || Math.abs(bubble.right - reply.right) > 1) throw new Error('User message is not aligned to the right')
    })()`)
    writeFileSync(path.join(__dirname, '../test-artifacts', `codex-conversation-${width}.png`), (await window.webContents.capturePage()).toPNG())
    await window.webContents.executeJavaScript(`(async () => {
      document.querySelector('[aria-label="选择技能"]').click()
      await new Promise(resolve => setTimeout(resolve, 100))
      const menu = document.querySelector('.skill-menu')
      const bounds = menu.getBoundingClientRect()
      if(bounds.left < 0 || bounds.right > innerWidth || bounds.top < 52 || menu.scrollWidth > menu.clientWidth + 1) throw new Error('Skill menu escapes the viewport at ${width}px')
      if(bounds.bottom > document.querySelector('.task-composer').getBoundingClientRect().top) throw new Error('Skill menu covers the draft')
      await new Promise(resolve => setTimeout(resolve, 100))
    })()`)
    await window.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true})
    writeFileSync(path.join(__dirname, '../test-artifacts', `conversation-skills-${width}.png`), (await window.webContents.capturePage(undefined, {stayHidden:true})).toPNG())
    if (width === 1000) {
      await window.webContents.executeJavaScript(`(async () => {
        document.querySelector('.product-v3').classList.add('dark')
        await new Promise(resolve => setTimeout(resolve, 150))
      })()`)
      await window.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true})
      writeFileSync(path.join(__dirname, '../test-artifacts/conversation-skills-dark.png'), (await window.webContents.capturePage(undefined, {stayHidden:true})).toPNG())
      await window.webContents.executeJavaScript(`document.querySelector('.product-v3').classList.remove('dark')`)
    }
    await window.webContents.executeJavaScript(`document.querySelector('[aria-label="关闭技能菜单"]').click()`)
  }
  window.setContentSize(1000, 1040)
  await window.webContents.executeJavaScript(`(async () => {
    const until = async predicate => { for(let i=0;i<160;i++){ if(predicate()) return; await new Promise(resolve=>setTimeout(resolve,40)) }; throw new Error('Rich content condition timed out') }
    const input = document.querySelector('[aria-label="任务输入"]')
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'验证图表')
    input.dispatchEvent(new Event('input',{bubbles:true}))
    await until(() => !document.querySelector('[aria-label="提交任务"]').disabled)
    document.querySelector('[aria-label="提交任务"]').click()
    await until(() => document.querySelector('.is-active-response .modern-code-block'))
    await until(() => document.querySelector('.is-active-response .svg-preview-frame'))
    await until(() => !document.querySelector('.is-active-response'))
    await until(() => document.querySelector('.modern-mermaid-block .svg-preview-frame'))
    const mermaid = document.querySelector('.modern-mermaid-block')
    if(!mermaid.querySelector('iframe').srcdoc.includes('<svg')) throw new Error('Mermaid did not render SVG')
    if(!mermaid.querySelector('iframe').srcdoc.includes('JetBrains Mono') || !mermaid.querySelector('iframe').srcdoc.includes('data:font/woff2;base64,')) throw new Error('Mermaid preview is missing its bundled font')
    const button = (root, title) => [...root.querySelectorAll('button')].find(element => element.title === title)
    button(mermaid,'查看图表源代码').click()
    await until(() => mermaid.querySelector('pre'))
    if(!mermaid.querySelector('pre').textContent.includes('graph TD')) throw new Error('Mermaid source is unavailable')
    button(mermaid,'切换为可视化图表').click()
    await until(() => mermaid.querySelector('iframe'))
    const svg = [...document.querySelectorAll('.modern-code-block')].find(element => element.querySelector('.modern-code-lang')?.textContent === 'SVG')
    if(!svg.querySelector('iframe').srcdoc.includes('<circle')) throw new Error('SVG preview lost the drawing')
    button(svg,'查看 SVG 源代码').click()
    await until(() => svg.querySelector('pre'))
    button(svg,'预览 SVG 图表').click()
    await until(() => svg.querySelector('iframe'))
    if(!svg.querySelector('[aria-label="全屏查看图表"]') || !svg.querySelector('[aria-label="下载 SVG"]')) throw new Error('SVG controls are missing')
    const code = [...document.querySelectorAll('.modern-code-block')].find(element => element.querySelector('.modern-code-lang')?.textContent === 'TYPESCRIPT')
    if(!code.querySelector('.token.keyword') || !code.querySelector('[title="复制代码"]')) throw new Error('Code highlighting or copy control is missing')
    mermaid.scrollIntoView({block:'center'})
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })()`)
  writeFileSync(path.join(__dirname, '../test-artifacts/conversation-rich-content.png'), (await window.webContents.capturePage()).toPNG())
  for (const [selector, format] of [['.modern-code-block', 'SVG'], ['.modern-code-block', 'PNG'], ['.modern-mermaid-block', 'SVG'], ['.modern-mermaid-block', 'PNG']]) {
    const downloaded = new Promise((resolve, reject) => {
      window.webContents.session.once('will-download', (_event, item) => {
        const target = path.join(process.env.DESKTOP_SMOKE_DIR, item.getFilename())
        item.setSavePath(target)
        item.once('done', (_event, state) => state === 'completed' ? resolve(target) : reject(new Error('Download failed: ' + state)))
      })
    })
    await window.webContents.executeJavaScript(`document.querySelector('${selector} [title="下载 ${format}"]').click()`)
    const file = await downloaded
    const bytes = readFileSync(file)
    if(format === 'PNG') assert.equal(bytes.subarray(1, 4).toString(), 'PNG')
    else {
      assert.match(bytes.toString(), /<svg/)
      if(selector === '.modern-mermaid-block') assert.match(bytes.toString(), /data:font\/woff2;base64,/)
    }
  }
  window.setContentSize(1280, 1100)
  await window.webContents.executeJavaScript(`(async () => {
    const until = async predicate => { for(let i=0;i<120;i++){ if(predicate()) return; await new Promise(resolve=>setTimeout(resolve,30)) }; throw new Error('Staged streaming condition timed out') }
    document.querySelector('[aria-label="新建任务"]').click()
    await until(() => document.querySelector('.task-empty'))
    const input = document.querySelector('[aria-label="任务输入"]')
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'验证分段流式')
    input.dispatchEvent(new Event('input',{bubbles:true}))
    await until(() => !document.querySelector('[aria-label="提交任务"]').disabled)
    document.querySelector('[aria-label="提交任务"]').click()
    await until(() => document.querySelector('.is-active-response')?.textContent.includes('问题已经定位'))
    const active = document.querySelector('.is-active-response')
    const segments = [...active.querySelectorAll('.run-segment')]
    if(segments.map(element => element.classList.contains('run-tool-group') ? 'tools' : 'text').join('|') !== 'text|tools|text|tools') throw new Error('Live tools are not interleaved with narration')
    if(active.querySelectorAll('.act-item').length !== 4) throw new Error('Live tools were duplicated across stages')
    if(active.querySelectorAll('.inline-activity-card.is-active').length !== 1) throw new Error('Finished stages still show a spinner')
    segments[1].querySelector('details').open = true
    await until(() => !document.querySelector('.is-active-response'))
    const saved = [...document.querySelectorAll('.saved-run-activity')].at(-1)
    if(saved.querySelectorAll('.run-tool-group').length !== 3 || saved.querySelectorAll('.act-item').length !== 6) throw new Error('Saved stage boundaries changed')
    if(document.body.innerText.split('问题已经定位').length !== 2) throw new Error('Narration was duplicated after saving')
    saved.querySelector('.inline-activity-card').open = true
    const pane = document.querySelector('.conversation')
    pane.scrollTop += saved.getBoundingClientRect().top - pane.getBoundingClientRect().top - 24
  })()`)
  for(const dark of [false, true]) {
    await window.webContents.executeJavaScript(`(async () => {
      document.querySelector('.product-v3').classList.toggle('dark', ${dark})
      await new Promise(resolve=>setTimeout(resolve,80))
      const groups = [...document.querySelectorAll('.saved-run-activity')].at(-1).querySelectorAll('.run-tool-group')
      for(const group of groups) if(group.scrollWidth > group.clientWidth + 1) throw new Error('Stage tool summary overflows')
    })()`)
    await window.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true})
    writeFileSync(path.join(__dirname, '../test-artifacts', 'streaming-stages-'+(dark ? 'dark' : 'light')+'.png'), (await window.webContents.capturePage(undefined, {stayHidden:true})).toPNG())
  }
  console.log('Electron smoke passed: interleaved live/saved narration, tools, keyboard skills, responsive themes, SVG/Mermaid and downloads')
  clearTimeout(deadline)
  window.destroy()
  app.quit()
}).catch(error => { console.error(error); clearTimeout(deadline); app.exit(1) })
