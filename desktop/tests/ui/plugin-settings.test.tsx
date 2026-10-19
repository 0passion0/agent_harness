import { expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { UserConfig, type UserSettingsPageProps } from '../../src/renderer/src/plugins/user_config'

const noop = async () => {}
const props: UserSettingsPageProps = {
  initialTab: 'plugins', application: {
    build_mode: 'production', projects: [], active_project_id: null, model: null,
    user: null, runtimes: { python: '', node: '' }, skills: [],
    plugins: [
      { id: 'research', name: 'Research', description: 'Research tools', version: '1.0', workflows: [{ name: 'Search', description: 'Search sources' }], interaction_kinds: [], enabled: true, configured: true, loaded: true }
    ],
    permission_mode: 'ask', data_dir: 'data'
  }, config: null, busy: false, apply: noop, inspect: noop, save: noop, remove: noop,
  toggleSkill: noop, testMcp: noop, importSkills: noop, testProfile: async () => '',
  readSkill: async () => null, saveSkill: noop
}

test('plugin settings lists installed plugins and their status without source editing', () => {
  const production = renderToStaticMarkup(<UserConfig {...props}/>)
  expect(production).toContain('启用 Research')
  expect(production).toContain('v1.0')
  expect(production).toContain('已启用')
  expect(production).toContain('Search sources')
  expect(production).not.toContain('插件开发')
  const development = renderToStaticMarkup(<UserConfig {...props} application={{ ...props.application!, build_mode: 'development' }}/>)
  expect(development).toContain('Research tools')
  expect(development).not.toContain('导出 ZIP')
})

test('model settings displays the saved code and controls', () => {
  for (const code of ['agnes-3.0-flash', 'custom-model-code']) {
    const html = renderToStaticMarkup(<UserConfig {...props} initialTab="models" application={{ ...props.application!, model: { provider: 'medpeer', model: code }, max_context_tokens: 475000 }}/>)
    expect(html).toContain(`value="${code}"`)
    expect(html).toContain('保存模型')
    expect(html).toContain('接口地址与凭据由 Python 后端配置')
    expect(html).not.toContain('使用默认 Agnes')
    expect(html).toContain('上下文窗口与最大 Token')
    expect(html).toContain('475,000 Token')
  }
})

