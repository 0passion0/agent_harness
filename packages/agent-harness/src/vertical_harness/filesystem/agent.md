# 文件能力

DocumentFilesystemBackend 适配框架文件接口；DocumentText 提取文档；读取、编辑、补丁、搜索和格式化分别由对应类负责。HarnessResources 管指令与技能文件。

- 不导入 bridge、runtime 或桌面入口。格式化只借用 execution/ProcessControl。
- FileMutation 统一写锁、原子字节写入和取消；失败清理临时文件。
- 补丁先完整预检、按顺序提交、按最终路径报告；部分失败必须列出已完成操作。
- 保留权限、路径边界、BOM、换行、分页和输出上限；ZIP 导入保持链接、大小与重名检查。
