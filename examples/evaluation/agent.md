# 评测示例目录 (examples/evaluation) - Agent 指南与规范

## 目录定位与职责
`examples/evaluation/` 专门存放系统离线评估（Offline Evaluation）和基准回归所需的结构化样例文件。

## 核心文件
- `citations.jsonl`：学术引文核验数据集样本。每行代表一个独立的学术核查案例，包含待审计手稿片段（`manuscript_text`）、参考文献列表（`bibliography`）以及预期的审计断言标签。

## Agent 交互与数据维护指引
1. **数据一致性**：数据集每一项必须通过 `vertical_harness.evaluation.datasets` 的校验逻辑。
2. **场景覆盖度**：
   - 包含完全合规的规范引文。
   - 包含超出范围的悬挂引文（Dangling citation）。
   - 包含引文标号冲突或排版错误的情况。
3. **安全与隐私**：测试样本中的文献信息和手稿文本必须使用公开文献或合成文本，不得含有私密文献。
