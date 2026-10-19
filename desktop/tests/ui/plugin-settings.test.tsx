import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { UserConfig } from '../../src/renderer/src/plugins/user_config'
import type { UserSettingsPageProps } from '../../src/renderer/src/plugins/settings'

const noop = async () => {}
const props: UserSettingsPageProps = {
  initialTab: 'plugins', application: {
    build_mode: 'production', projects: [], active_project_id: null, model: null,
    exa: { configured: false }, user: null, runtimes: { python: '', node: '' },
    plugins: [
      { id: 'data_analysis', name: '数据分析', description: '', interaction_kinds: [], enabled: true, configured: true, loaded: true }
    ],
    permission_mode: 'ask', data_dir: 'data'
  }, config: null, busy: false, apply: noop, inspect: noop, save: noop, remove: noop,
  toggleSkill: noop, testMcp: noop, importSkills: noop, testProfile: async () => '',
  readSkill: async () => null, saveSkill: noop
}

test('plugin settings shows enabled status and limits source editing to development', () => {
  const production = renderToStaticMarkup(<UserConfig {...props}/>)
  expect(production).toContain('启用 数据分析')
  expect(production).toContain('已加载')
  expect(production).not.toContain('数据分析插件开发')
  const development = renderToStaticMarkup(<UserConfig {...props} application={{ ...props.application!, build_mode: 'development' }}/>)
  expect(development).toContain('数据分析插件开发')
  expect(development).toContain('导出 ZIP')
})

test('model settings displays the saved code and controls', () => {
  for (const code of ['agnes-3.0-flash', 'custom-model-code']) {
    const html = renderToStaticMarkup(<UserConfig {...props} initialTab="models" application={{ ...props.application!, model: { provider: 'medpeer', model: code }, max_context_tokens: 475000 }}/>)
    expect(html).toContain(`value="${code}"`)
    expect(html).toContain('保存模型')
    expect(html).toContain('使用默认 Agnes')
    expect(html).toContain('上下文窗口与最大 Token')
    expect(html).toContain('475,000 Token')
  }
})

