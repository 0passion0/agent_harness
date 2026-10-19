const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const assert = require('node:assert/strict')
const { mkdirSync, writeFileSync } = require('node:fs')
const screenshots = path.resolve(__dirname, '../test-artifacts')
mkdirSync(screenshots, { recursive: true })
app.setPath('appData', process.env.DESKTOP_SMOKE_DIR)
let timer = setTimeout(() => { console.error('Desktop smoke timed out'); app.exit(1) }, 25000)
app.on('browser-window-created', (_event, window) => {
  window.webContents.setBackgroundThrottling(false)
  window.webContents.on('preload-error', (_event, _path, error) => { console.error(error); app.exit(1) })
  window.webContents.once('did-finish-load', async () => {
    try {
      const result = await window.webContents.executeJavaScript(`new Promise(resolve => {
        function check() { if(document.querySelector('.auth-submit')) resolve({bridge:typeof window.medpeer.command,text:document.body.innerText}); else requestAnimationFrame(check); } check();
      })`)
      assert.equal(result.bridge, 'function'); assert.match(result.text, /登录 MedPeer/)
      const initial = await window.webContents.executeJavaScript(`window.medpeer.command({id:'startup-check',method:'app.snapshot',params:{}})`)
      assert.equal(initial.user, null)
      if (process.env.ELECTRON_RENDERER_URL) {
        console.log('Electron development URL startup IPC passed:', window.webContents.getURL())
        clearTimeout(timer); app.quit(); timer = setTimeout(() => app.exit(1), 5000)
        return
      }
      writeFileSync(path.join(screenshots, 'login.png'), (await window.webContents.capturePage()).toPNG())
      const profile = { userID: 7, realName: 'Smoke', userName: 'smoke', userAvatar: '', userPhone: '', userCredit: 0, isCo: false, isBeta: false }
      const plugins = [{ id: 'research.citations', name: '引用核查', description: '核查参考文献', version: '1.0', workflows: [{ name: 'check_references', description: '检查引用' }], enabled: true, configured: true, loaded: true, interaction_kinds: ['plugin_authorization'] }]
      const skills = [{ id: 'global:citations', name: '引用写作规范', description: '格式规范', source: '全局', enabled: true }]
      const state = { user: profile, build_mode: 'development', projects: [{ id: 'project', name: '测试项目', path: process.env.DESKTOP_SMOKE_DIR }], active_project_id: 'project', model: {provider:'deepseek',model:'deepseek-flash'}, plugins,skills,runtimes:{python:'',node:''},permission_mode:'ask',data_dir:process.env.DESKTOP_SMOKE_DIR,max_context_tokens:128000 }
      const workflowRecords = [
        { kind: 'workflow', seq: 1, operation_id: 'workflow-1', plugin_id: 'research.citations', workflow_name: 'check_references', status: 'running' },
        { kind: 'workflow.node', seq: 2, operation_id: 'node-1', parent_ids: ['workflow-1'], node: 'verify', status: 'running' },
        { kind: 'workflow.node', seq: 3, operation_id: 'node-1', parent_ids: ['workflow-1'], node: 'verify', status: 'completed', summary: '已核查 3 条引用' },
        { kind: 'workflow', seq: 4, operation_id: 'workflow-1', plugin_id: 'research.citations', workflow_name: 'check_references', status: 'completed' }
      ]
      let session = 'thread', messages = [], runs = [], run = null
      const commands = [], errors = []
      const snapshot = () => ({session_id:session,thread_id:session,thread_ids:[session],messages,attachments:[],artifacts:[],runs,totals:{},unsettled_history:false})
      const sessions = () => [{id:session,title:'测试会话',updated:new Date().toISOString()}]
      const emptyUsage = { requests:0,reported_requests:0,input_tokens:0,output_tokens:0,total_tokens:0,cache_read:null,reasoning:null,cost_estimate:null,unpriced_requests:0,jev_requests:0 }
      const send = frame => window.webContents.send('backend:event',frame)
      const finish = status => { const current = run; run = null; messages = [{type:'human',data:{id:'user-1',content:'smoke hello'}},{type:'ai',data:{content:'smoke reply'}}]; runs = [{id:current,user_message_id:'user-1',status,records:workflowRecords,requests:[],tools:{},elapsed:1}]; send({event:'run.finished',session_id:session,run_id:current,data:{status,snapshot:snapshot()}}) }
      ipcMain.removeHandler('backend:command')
      ipcMain.removeHandler('workspace:list-files')
      ipcMain.handle('workspace:list-files', () => [])
      ipcMain.handle('backend:command', (_event, command) => {
        commands.push(command)
        switch(command.method) {
          case 'app.snapshot': return state
          case 'session.list': case 'session.search': return sessions()
          case 'session.snapshot': return snapshot()
          case 'plugin.list': return plugins
          case 'plugin.snapshot': return {plugin_id:command.params.plugin_id,state:{}}
          case 'session.clear': session = 'new-thread'; messages=[];runs=[];return snapshot()
          case 'session.resume': session=command.params.session_id;return snapshot()
          case 'config.inspect': return {agent:{content:'',source:''},skills:[],mcp:{servers:{},sources:{}},overrides:{agent:'',mcp:''}}
          case 'usage.stats': return {timezone:'Asia/Shanghai',today:'2026-10-19',tracking_since:null,dates:[],model_names:[],totals:emptyUsage,models:[],daily:[]}
          case 'app.environment': return {python_executable:'python',python_version:'3.12',platform:'win32',data_dir:process.env.DESKTOP_SMOKE_DIR,project_path:process.env.DESKTOP_SMOKE_DIR}
          case 'run.start':
            run=command.id
            setTimeout(() => { workflowRecords.forEach(data => send({event:'run.event',session_id:session,run_id:run,data})); send({event:'run.event',session_id:session,run_id:run,data:{kind:'text',text:'smoke reply'}}); finish('complete') },60)
            return {run_id:run}
          default: errors.push(command.method);throw new Error(`Unexpected smoke command ${command.method}`)
        }
      })
      const reloaded = new Promise(resolve => window.webContents.once('did-finish-load', resolve))
      window.webContents.reload();await reloaded
      const checked = await window.webContents.executeJavaScript(`(async () => {
        async function until(check) { const deadline=Date.now()+10000;while(!check()){if(Date.now()>deadline)throw new Error('UI timeout: '+document.body.innerText);await new Promise(resolve=>setTimeout(resolve,10));} }
        function click(text,selector='button'){const el=[...document.querySelectorAll(selector)].find(b=>b.textContent.trim()===text);if(!el)throw new Error('Missing button '+text);el.click();}
        await until(()=>document.querySelector('.task-composer')&&!document.querySelector('textarea').disabled);
        if(!document.querySelector('.product-v3')||!document.querySelector('.app-rail')||!document.querySelector('.sidebar'))throw new Error('Original layout missing');
        const textarea=document.querySelector('textarea');textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'/',bubbles:true,cancelable:true}));
        await until(()=>document.querySelector('.capability-menu'));
        [...document.querySelectorAll('[role="menuitem"]')].find(item=>item.textContent.startsWith('插件')).click();
        await until(()=>document.querySelector('[role="menuitemcheckbox"]'));
        if(document.querySelector('.capability-menu').textContent.includes('引用写作规范'))throw new Error('Skills mixed into plugin list');
        document.querySelector('[role="menuitemcheckbox"]').click();click('‹ 返回');
        await until(()=>[...document.querySelectorAll('[role="menuitem"]')].some(item=>item.textContent.startsWith('技能')));
        [...document.querySelectorAll('[role="menuitem"]')].find(item=>item.textContent.startsWith('技能')).click();
        await until(()=>document.querySelector('[role="menuitemcheckbox"]'));
        if(document.querySelector('.capability-menu').textContent.includes('引用核查'))throw new Error('Plugins mixed into skill list');
        document.querySelector('[role="menuitemcheckbox"]').click();click('完成');
        await until(()=>document.querySelectorAll('.capability-chip').length===2);
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(textarea,'smoke hello');textarea.dispatchEvent(new Event('input',{bubbles:true}));
        await until(()=>!document.querySelector('[aria-label="提交任务"]').disabled);document.querySelector('[aria-label="提交任务"]').click();
        await until(()=>document.body.innerText.includes('smoke reply')&&document.querySelector('[aria-label="提交任务"]'));
        await until(()=>document.querySelector('.workflow-card-heading'));
        document.querySelector('.workflow-card-heading').click();
        await until(()=>document.querySelector('.workflow-card').textContent.includes('已核查 3 条引用'));
        if(!document.querySelector('.workflow-card-heading').textContent.includes('已完成'))throw new Error('Workflow terminal status missing');
        click('设置与连接');await until(()=>document.querySelector('.settings-content-scroll'));
        const tabs=['全局模型','插件与权限','指令与 Skills','MCP 服务','个人信息','研值','Token 消耗'];
        for(const label of tabs){click(label,'.settings-navigation button');await until(()=>document.querySelector('.settings-main-header h1')?.textContent===label);await new Promise(resolve=>setTimeout(resolve,40));}
        click('返回任务');await until(()=>document.querySelector('.task-composer'));
        document.querySelector('[aria-label="切换执行详情"]').click();await until(()=>document.querySelector('.task-detail'));
        for(const label of ['目录','研究','过程']){click(label,'.detail-tabs button');await new Promise(resolve=>setTimeout(resolve,30));}
        document.querySelector('[aria-label="关闭任务详情"]').click();
        click('深色外观');await until(()=>document.querySelector('.product-v3.dark'));
        document.querySelector('.new-chat').click();await until(()=>document.querySelector('.task-empty'));
        return {tabs:tabs.length,text:document.body.innerText,dark:document.querySelector('.product-v3').classList.contains('dark')};
      })()`)
      assert.equal(checked.tabs,7);assert.equal(checked.dark,true);assert.deepEqual(errors,[])
      await new Promise(resolve => setTimeout(resolve, 250))
      writeFileSync(path.join(screenshots, 'workspace-dark.png'), (await window.webContents.capturePage()).toPNG())
      assert.equal(commands.filter(c=>c.method==='run.start').length,1)
      const submitted = commands.find(c=>c.method==='run.start').params
      assert.deepEqual(submitted.selected_plugins,['research.citations']);assert.deepEqual(submitted.selected_skills,['global:citations'])
      console.log('Electron smoke passed: original login/layout, preload, separate slash catalogs, selected capability transport, inline workflow, streamed chat, 7 settings tabs, 3 detail tabs, theme and new task')
      clearTimeout(timer);app.quit();timer=setTimeout(()=>app.exit(1),5000)
    } catch(error){ console.error(error);app.exit(1) }
  })
})
void import(pathToFileURL(path.resolve(__dirname,'../out/main/index.js')).href)
