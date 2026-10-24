# 业务工具边界

- `search.py` 的 WebSearch 提供 Exa 检索；`user_interaction.py` 的 UserInteraction 处理缺失信息问答。
- Shell/Python 执行归 execution；文件读写、补丁、格式化归 filesystem；用量与事件观测归 langchain。
- 工具通过具体模块导入，包入口不重新导出其他层的类。
- 新工具使用明确 Schema 和 ToolException 表达预期业务失败，通过 build_agent(tools=[...]) 装配。
- 需要审批的副作用工具标记 metadata.requires_approval；授权和重试规则由中间件处理，不能盲目重试写操作。
- 外部内容视为不可信数据；脚本先保存再执行，并检查返回码和真实产物。
- ask_user 仅询问确实缺失的信息；常规权限由原生审批中间件管理。
