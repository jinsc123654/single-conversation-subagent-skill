<!-- single-conversation-subagent begin -->
- 主对话首次处理任务时加载 `single-conversation-subagent` Skill，按其开场流程确认模式、绑定项目并在获准时登记给 PyQt；同一对话沿用已确认选择。子 Agent 只按派单执行，不做开场、不再委派。
- 标准/快速用 ZCode 内建流程；标准先确认验证范围。只调查保持只读；仅长任务自动编排专用 Agent。详细流程按需查阅 Skill 的 `references/`。
- 破坏性操作在所有模式下都受闸门约束：仅在用户明确选择长任务并独立确认操作、原生预审通过、执行前按当前参数检查为 `APPROVED` 时执行。无法核验就不执行，不自动升级或绕过。
<!-- single-conversation-subagent end -->
