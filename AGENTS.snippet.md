<!-- single-conversation-subagent begin -->
- 用户点名 ZCode Agent 就按其注册身份派单，遵守任务授权。清楚的快速、标准或只读任务直接按范围处理，日常不要求先搜 SCS 工具、开会话或弹窗。只读可调用 `查询侦察员`；标准任务可按需直接委派专用执行角色。
- 长任务和需要协作记录时加载 `single-conversation-subagent` Skill，沿用已选模式，一张卡片确认必要的验证和复核策略，首次正式复核才选审阅员。子 Agent 只按派单工作。
- 破坏性操作在所有模式下都受闸门约束：仅在用户明确选择长任务并独立确认操作、原生预审通过、执行前按当前参数检查为 `APPROVED` 时执行。无法核验就不执行，不自动升级或绕过。
<!-- single-conversation-subagent end -->
