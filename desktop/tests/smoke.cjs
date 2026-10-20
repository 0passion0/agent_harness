const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const assert = require('node:assert/strict')
const { mkdirSync, writeFileSync } = require('node:fs')
app.disableHardwareAcceleration()
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
        function check() { if(document.querySelector('.task-composer')) resolve({bridge:typeof window.medpeer.command,text:document.body.innerText}); else requestAnimationFrame(check); } check();
      })`)
      assert.equal(result.bridge, 'function')
      const initial = await window.webContents.executeJavaScript(`window.medpeer.command({id:'startup-check',method:'app.snapshot',params:{}})`)
      assert.ok(initial.user === undefined)
      if (process.env.ELECTRON_RENDERER_URL) {
        console.log('Electron development URL startup IPC passed:', window.webContents.getURL())
        clearTimeout(timer); app.quit(); timer = setTimeout(() => app.exit(1), 5000)
        return
      }
      writeFileSync(path.join(screenshots, 'app.png'), (await window.webContents.capturePage(undefined, { stayAwake: true })).toPNG())
      const plugins = [{ id: 'research.citations', name: '引用核查', description: '核查参考文献', version: '1.0', workflows: [{ name: 'check_references', description: '检查引用' }], enabled: true, configured: true, loaded: true, interaction_kinds: ['plugin_authorization'] }]
      const skills = [{ id: 'global:citations', name: '引用写作规范', description: '格式规范', source: '全局', enabled: true }]
      const state = { build_mode: 'development', projects: [{ id: 'project', name: '测试项目', path: process.env.DESKTOP_SMOKE_DIR }], active_project_id: 'project', model: {provider:'deepseek',model:'deepseek-flash'}, plugins,skills,runtimes:{python:'',node:''},permission_mode:'ask',data_dir:process.env.DESKTOP_SMOKE_DIR,max_context_tokens:128000 }
      const workflowRecords = [
        { kind: 'workflow', seq: 1, operation_id: 'workflow-1', plugin_id: 'research.citations', workflow_name: 'check_references', status: 'running' },
        { kind: 'workflow.node', seq: 2, operation_id: 'node-1', parent_ids: ['workflow-1'], node: 'verify', status: 'running' },
        { kind: 'workflow.node', seq: 3, operation_id: 'node-1', parent_ids: ['workflow-1'], node: 'verify', status: 'completed', summary: '已核查 3 条引用' },
        { kind: 'workflow', seq: 4, operation_id: 'workflow-1', plugin_id: 'research.citations', workflow_name: 'check_references', status: 'completed' }
      ]
      let session = 'thread', messages = [], runs = [], run = null
      let layoutActiveRun
      const commands = [], errors = []
      const snapshot = () => ({session_id:session,thread_id:session,thread_ids:[session],messages,attachments:[],artifacts:[],runs,totals:{},unsettled_history:false,...(layoutActiveRun ? {active_run:layoutActiveRun} : {})})
      const sessions = () => [{id:session,title:'测试会话',updated:new Date().toISOString()}]
      const emptyUsage = { requests:0,reported_requests:0,input_tokens:0,output_tokens:0,total_tokens:0,cache_read:null,reasoning:null,cost_estimate:null,unpriced_requests:0,jev_requests:0 }
      const send = frame => window.webContents.send('backend:event',frame)
      const questions = [
        { interaction_id:'plugin',kind:'plugin_authorization',plugin_id:'research.citations',question:'Allow plugin?',purpose:'核查参考文献',input_scope:['references.txt'],allowed_effects:['read','external'] },
        { interaction_id:'custom',kind:'question',plugin_id:'harness',question:'希望如何组织报告？',options:['简要摘要','完整报告'] },
        { interaction_id:'choice',kind:'question',plugin_id:'harness',question:'选择报告语言',options:['中文','英文'] },
        { interaction_id:'text',kind:'question',plugin_id:'harness',question:'补充研究目标' },
        { interaction_id:'action',kind:'approval',plugin_id:'harness',question:'确认操作',action_requests:[{name:'task',args:{description:'分析商品价格与累计浏览量的关系。\n输入 JSON：\n'+JSON.stringify({task_type:'correlation',file_path:'/workspace/商品审查.csv',columns:['价格','累计浏览量'],analysis_goal:'排除无效数据后计算相关性，并说明有效样本量。'})+'\n注意：保留原始文件。',subagent_type:'数据分析'}},{name:'write_file',args:{path:'/workspace/report.md',description:'写入 report.md'}}] }
      ]
      let questionIndex = 0
      const ask = () => send({event:'interaction.request',session_id:session,run_id:run,data:questions[questionIndex]})
      const report = [
        '# 数据分析插件测试结果',
        'smoke reply：已核对工作区中的两个 CSV，结果如下。',
        '## 一、逐项状态',
        '| 步骤 | 输入 | 结果 |',
        '| --- | --- | --- |',
        '| profile | `参考图尺寸.csv` | **passed**：6 行 × 4 列，宽 / 高 / 宽高比均为数值 |',
        '| full_analysis / correlation | `包含较长中文名称的商品价格与累计浏览量调查数据.csv` | **failed**：`Audit failed: artifact SHA256 does not match execution receipt` |',
        '| 串行重跑 | [分析报告](artifacts/report.md) | **passed**：r = −0.151976，n = 31 |',
        '## 二、独立核对的数据',
        '- 价格数值条数 31，已排除 4 条缺失值。\n- 均值 111.27，中位数 99.0。',
        '```python\nresult = analyze(data)\nprint(result)\n```',
        ...Array.from({length:8}, () => '已核对原始数据，报告中的数值与执行记录一致。')
      ].join('\n\n').replace(/\n\n(?=\|)/g, '\n')
      const finish = status => { const current = run; run = null; messages = [{type:'human',data:{id:'user-1',content:'smoke hello'}},{type:'ai',data:{content:'先检查工作区中的文件和字段。'}},{type:'ai',data:{content:'两个表已完成画像，开始核对统计结果。'}},{type:'ai',data:{content:report}}]; runs = [{id:current,user_message_id:'user-1',status,records:workflowRecords,requests:[],tools:{},elapsed:1}]; send({event:'run.finished',session_id:session,run_id:current,data:{status,snapshot:snapshot()}}) }
      ipcMain.removeHandler('backend:command')
      ipcMain.removeHandler('workspace:list-files')
      ipcMain.handle('workspace:list-files', () => [])
      ipcMain.handle('backend:command', async (_event, command) => {
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
            setTimeout(ask,60)
            return {run_id:run}
          case 'interaction.answer':
            await window.webContents.executeJavaScript(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`)
            assert.equal(command.params.interaction_id, questions[questionIndex].interaction_id)
            assert.equal(command.params.answer, ['approve','双语摘要','英文','核查临床研究','approve'][questionIndex])
            writeFileSync(path.join(screenshots, `decision-${questions[questionIndex].interaction_id}.png`), (await window.webContents.capturePage(undefined, { stayAwake: true })).toPNG())
            if (questionIndex === 1) {
              await window.webContents.executeJavaScript(`document.querySelector('[aria-label="切换侧边栏"]').click()`)
              window.setSize(720, 720)
              await window.webContents.executeJavaScript(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`)
              const fits = await window.webContents.executeJavaScript(`(() => { const bounds=document.querySelector('.decision').getBoundingClientRect();return bounds.left>=0&&bounds.right<=innerWidth+1&&bounds.top>=0&&bounds.bottom<=innerHeight+1; })()`)
              assert.equal(fits, true, 'Question card must fit a narrow window')
              writeFileSync(path.join(screenshots, 'decision-narrow.png'), (await window.webContents.capturePage(undefined, { stayAwake: true })).toPNG())
              window.setSize(1440, 900)
              await window.webContents.executeJavaScript(`document.querySelector('[aria-label="切换侧边栏"]').click()`)
            }
            questionIndex++
            setTimeout(() => {
              if(questionIndex < questions.length) ask()
              else { workflowRecords.forEach(data => send({event:'run.event',session_id:session,run_id:run,data})); send({event:'run.event',session_id:session,run_id:run,data:{kind:'text',text:'smoke reply'}}); finish('complete') }
            },60)
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
        await until(()=>document.querySelector('.capability-palette'));
        [...document.querySelectorAll('.capability-item')].find(item=>item.querySelector('.capability-item-name')?.textContent==='引用核查').click();
        await until(()=>!document.querySelector('.capability-palette'));
        document.querySelector('[aria-label="选择扩展能力与快捷指令 (/)"]').click();
        await until(()=>document.querySelector('.capability-palette'));
        [...document.querySelectorAll('.capability-item')].find(item=>item.querySelector('.capability-item-name')?.textContent==='引用写作规范').click();
        await until(()=>document.querySelectorAll('.capability-chip').length===2);
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(textarea,'smoke hello');textarea.dispatchEvent(new Event('input',{bubbles:true}));
        await until(()=>!document.querySelector('[aria-label="提交任务"]').disabled);document.querySelector('[aria-label="提交任务"]').click();
        await until(()=>document.querySelector('.plugin-decision'));
        if(!document.querySelector('.plugin-decision').textContent.includes('引用核查'))throw new Error('Plugin identity missing');
        if(!document.querySelector('.plugin-decision').textContent.includes('无需重复确认'))throw new Error('Durable grant explanation missing');
        click('授权并记住');
        await until(()=>document.body.innerText.includes('希望如何组织报告？'));
        if(document.querySelectorAll('.decision-option').length!==3)throw new Error('Choice/custom controls missing');
        document.querySelectorAll('.decision-option input')[2].click();
        await until(()=>document.querySelector('[aria-label="自定义回答"]'));
        const custom=document.querySelector('[aria-label="自定义回答"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(custom,'双语摘要');custom.dispatchEvent(new Event('input',{bubbles:true}));
        await until(()=>!document.querySelector('.decision button[type="submit"]').disabled);click('确认选择');
        await until(()=>document.body.innerText.includes('选择报告语言'));
        click('深色外观');
        document.querySelectorAll('.decision-option input')[1].click();
        await until(()=>!document.querySelector('.decision button[type="submit"]').disabled);click('确认选择');
        await until(()=>document.body.innerText.includes('补充研究目标'));
        click('浅色外观');
        const info=document.querySelector('[aria-label="补充信息"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(info,'核查临床研究');info.dispatchEvent(new Event('input',{bubbles:true}));
        await until(()=>!document.querySelector('.decision button[type="submit"]').disabled);click('提交答复');
        await until(()=>document.body.innerText.includes('写入 report.md'));
        if(document.querySelector('.decision').innerText.includes('task_type'))throw new Error('Raw JSON visible in approval');
        if(document.querySelector('.decision details').open)throw new Error('Long task details should start collapsed');
        document.querySelector('.decision details summary').click();
        if(!document.querySelector('.decision').innerText.includes('文件路径')||document.querySelector('.decision').innerText.includes('task_type'))throw new Error('Approval details must use readable fields');
        document.querySelector('.decision details summary').click();
        click('允许这次操作');
        await until(()=>document.body.innerText.includes('smoke reply')&&document.querySelector('[aria-label="提交任务"]'));
        await until(()=>document.querySelector('.workflow-card-heading'));
        document.querySelector('.saved-run-activity > details > summary').click();
        document.querySelector('.workflow-card-heading').click();
        await until(()=>document.querySelector('.workflow-card').textContent.includes('已核查 3 条引用'));
        if(!document.querySelector('.workflow-card-heading').textContent.includes('已完成'))throw new Error('Workflow terminal status missing');
        click('设置与连接');await until(()=>document.querySelector('.settings-content-scroll'));
        const tabs=['全局模型','插件与权限','指令与 Skills','MCP 服务','Token 消耗'];
        for(const label of tabs){click(label,'.settings-navigation button');await until(()=>document.querySelector('.settings-main-header h1')?.textContent===label);await new Promise(resolve=>setTimeout(resolve,40));}
        click('返回任务');await until(()=>document.querySelector('.task-composer'));
        document.querySelector('[aria-label="切换执行详情"]').click();await until(()=>document.querySelector('.task-detail'));
        for(const label of ['目录','研究','过程']){click(label,'.detail-tabs button');await new Promise(resolve=>setTimeout(resolve,30));}
        document.querySelector('[aria-label="关闭任务详情"]').click();
        click('深色外观');await until(()=>document.querySelector('.product-v3.dark'));
        document.querySelector('.new-chat').click();await until(()=>document.querySelector('.task-empty'));
        return {tabs:tabs.length,text:document.body.innerText,dark:document.querySelector('.product-v3').classList.contains('dark')};
      })()`)
      assert.equal(checked.tabs,5);assert.equal(checked.dark,true);assert.deepEqual(errors,[])
      // Reuse the completed report to check the actual CSS cascade in Chromium.
      session = 'layout-report'
      messages = [{type:'ai',data:{content:'先检查工作区中的文件和字段。'}},{type:'ai',data:{content:report}}]
      const layoutReloaded = new Promise(resolve => window.webContents.once('did-finish-load', resolve))
      window.webContents.reload();await layoutReloaded
      for (const [name, width, height, dark] of [['light',1440,900,false],['dark',1440,900,true],['narrow',640,720,false]]) {
        window.setSize(width,height)
        const layout = await window.webContents.executeJavaScript(`(async () => {
          while(!document.querySelector('.message-table-scroll'))await new Promise(resolve=>requestAnimationFrame(resolve));
          if(innerWidth<860&&document.querySelector('.sidebar'))document.querySelector('[aria-label="切换侧边栏"]').click();
          document.querySelector('.app').classList.toggle('dark',${dark});
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          const pane=document.querySelector('.conversation');pane.scrollTop=0;
          const rect=selector=>document.querySelector(selector).getBoundingClientRect();
          const table=document.querySelector('.message-table-scroll');
          const chip=document.querySelector('.file-link-chip');
          const first=document.querySelector('.message.ai');
          return {
            buttonWidth:rect('.file-chip-btn').width,buttonHeight:rect('.file-chip-btn').height,
            copyHeight:rect('.msg-copy-btn').height,chipHeight:chip.getBoundingClientRect().height,
            aligned:Math.abs(rect('.message-avatar').top-first.querySelector('p').getBoundingClientRect().top)<=4,
            separated:rect('.conversation-wrap').bottom<=rect('.task-bottom').top+1,
            fits:table.clientWidth<=rect('.message-content').width+1&&pane.scrollWidth<=pane.clientWidth+1,
            tableScrolls:table.scrollWidth>table.clientWidth,
            composerFits:rect('.task-composer').right<=innerWidth&&rect('.task-composer').bottom<=innerHeight,
            codePadding:getComputedStyle(document.querySelector('.modern-code-pre')).paddingTop
          };
        })()`)
        assert.equal(layout.buttonWidth,24);assert.equal(layout.buttonHeight,24)
        assert.equal(layout.copyHeight,28);assert.ok(layout.chipHeight<=30)
        assert.equal(layout.aligned,true);assert.equal(layout.separated,true)
        assert.equal(layout.fits,true);assert.equal(layout.composerFits,true)
        assert.equal(layout.codePadding,'14px')
        if(name==='narrow')assert.equal(layout.tableScrolls,true)
        await new Promise(resolve => setTimeout(resolve, 250))
        writeFileSync(path.join(screenshots, `conversation-${name}.png`), (await window.webContents.capturePage(undefined, { stayAwake: true })).toPNG())
      }
      session = 'layout-stream'
      const toolRecords = Array.from({length:24}, (_, index) => [
        {kind:'tool_start',call_id:`file-${index}`,name:'read_file',status:'running',arguments:{path:`data/table-${index}.csv`}},
        ...(index<23 ? [{kind:'tool_end',call_id:`file-${index}`,name:'read_file',status:'success',output:'已读取文件',elapsed:0.12}] : [])
      ]).flat()
      messages = [{type:'human',data:{id:'layout-user',content:'检查数据文件，并给出统计结果。'}}]
      runs = []
      layoutActiveRun = {id:'layout-run',started_at:'now',user_message_id:'layout-user',records:toolRecords}
      window.setSize(1000,800)
      const streamReloaded = new Promise(resolve => window.webContents.once('did-finish-load', resolve))
      window.webContents.reload();await streamReloaded
      await window.webContents.executeJavaScript(`(async()=>{while(!document.querySelector('.inline-activity-card'))await new Promise(resolve=>requestAnimationFrame(resolve));})()`)
      send({event:'run.event',session_id:session,run_id:'layout-run',data:{kind:'text',text:'正在整理数据，已读取 23 个文件。'}})
      const streaming = await window.webContents.executeJavaScript(`(async()=>{
        while(!document.querySelector('.inline-activity-card.is-active'))await new Promise(resolve=>requestAnimationFrame(resolve));
        const card=document.querySelector('.inline-activity-card');
        const compact=!card.open&&card.getBoundingClientRect().height<80;
        card.querySelector('summary').click();
        await new Promise(resolve=>requestAnimationFrame(resolve));
        const list=card.querySelector('.inline-activity-list');
        return {compact,items:card.querySelectorAll('.act-item').length,active:card.querySelectorAll('.act-item.is-running').length,bounded:list.clientHeight<=320&&list.scrollHeight>list.clientHeight};
      })()`)
      assert.deepEqual(streaming,{compact:true,items:24,active:1,bounded:true})
      await new Promise(resolve => setTimeout(resolve,250))
      writeFileSync(path.join(screenshots,'activity-streaming.png'),(await window.webContents.capturePage(undefined,{stayAwake:true})).toPNG())
      toolRecords.push({kind:'tool_end',call_id:'file-23',name:'read_file',status:'success',output:'已读取文件',elapsed:0.12})
      messages.push({type:'ai',data:{content:'已完成 24 个文件的检查，执行记录保留在下方，可随时展开查看。'}})
      runs = [{id:'layout-run',user_message_id:'layout-user',status:'complete',records:toolRecords,requests:[],tools:{},elapsed:1}]
      layoutActiveRun = undefined
      send({event:'run.finished',session_id:session,run_id:'layout-run',data:{status:'complete',snapshot:snapshot()}})
      for (const reload of [false,true]) {
        if(reload){const loaded=new Promise(resolve=>window.webContents.once('did-finish-load',resolve));window.webContents.reload();await loaded;}
        const saved = await window.webContents.executeJavaScript(`(async()=>{
          while(!document.querySelector('.saved-run-activity'))await new Promise(resolve=>requestAnimationFrame(resolve));
          const card=document.querySelector('.saved-run-activity > details');
          return {collapsed:!card.open,items:card.querySelectorAll('.act-item').length,active:card.querySelectorAll('.is-running').length,complete:card.textContent.includes('24 已完成')};
        })()`)
        assert.deepEqual(saved,{collapsed:true,items:24,active:0,complete:true})
      }
      await new Promise(resolve => setTimeout(resolve,250))
      writeFileSync(path.join(screenshots,'activity-complete.png'),(await window.webContents.capturePage(undefined,{stayAwake:true})).toPNG())
      await new Promise(resolve => setTimeout(resolve, 250))
      writeFileSync(path.join(screenshots, 'workspace-dark.png'), (await window.webContents.capturePage(undefined, { stayAwake: true })).toPNG())
      assert.equal(commands.filter(c=>c.method==='run.start').length,1)
      assert.equal(commands.filter(c=>c.method==='interaction.answer').length,5)
      const submitted = commands.find(c=>c.method==='run.start').params
      assert.deepEqual(submitted.selected_plugins,['research.citations']);assert.deepEqual(submitted.selected_skills,['global:citations'])
      console.log('Electron smoke passed: startup IPC, selected capabilities, approvals, light/dark/narrow layouts, compact streaming, merged tool calls, execution history after finish/reload, workflows, settings and new task')
      clearTimeout(timer);app.quit();timer=setTimeout(()=>app.exit(1),5000)
    } catch(error){ console.error(error);app.exit(1) }
  })
})
void import(pathToFileURL(path.resolve(__dirname,'../out/main/index.js')).href)
