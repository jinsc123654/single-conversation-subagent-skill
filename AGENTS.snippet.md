<!-- single-conversation-subagent begin -->
项目根优先用用户明确指定的目录，否则用当前对话工作目录。按 Skill 调用 session_open(project_root=<绝对项目根>, register_obsidian=true) 建库并登记给 PyQt。MCP 工作区配置只决定目录授权范围；登记独立于工作模式和验证范围。首次 session_open 前核对当前宿主审阅员的可用性；补登记或重连用原 session_id 和项目绝对路径恢复，细节见 Skill。
模式不明确才四选一；用户已经明确模式时直接登记。开场前至多一次直接相关的只读查询，设备和构建等确认后再进行。标准和长任务先确认验证范围。ZCode 长任务随后确认固定审阅员何时审查，标准和快速在固定审阅员已注册时确认是否安排收尾审查。每题按真实回复确认。
长任务沿用 SCS 工作包、依赖、prepare 和证据。ZCode 长任务先由 `方案顾问` 完成一次只读方案指导，主对话据此定方案和拆包；再把 analyze、implement、test 分别派给注册的 `查询侦察员`、`实施工程师`、`测试验证员`，独立任务按批并行，重叠写范围和共享设备串行。ZCode 的包级、收尾和操作预审都由 `固定审阅员` 独立只读完成。其他宿主沿用 Codex 流程。已有角色配置决定模型。具体见 Skill。
破坏性操作在所有模式都要先 operation_gate_record、执行前 operation_gate_check 为 APPROVED，并保留用户独立确认；ACCEPT 需报告唯一结论 + 用户确认，REJECT 对同 operation_id/digest 是终态，审阅员不可用只能记 UNAVAILABLE_OVERRIDE，不得伪记 ACCEPT；无宿主 Hook 时是协议级约束。工作包验收通过用 COMPLETED、重复或被取代用 ABANDONED 关闭并写原因；终态不可修改。已确认选择直接沿用。收到 QUESTION_PENDING 时按返回的原题号处理。纯 Skill 回退与细节见 SKILL.md 及其 references/。
docs-root 依次 00_doc/00_docs/docs/doc，均无建 docs；记忆与证据在其 agent/，WP 审查报告 04_test/report/。不迁移旧记录，SQLite 与手工 Markdown 恢复时显式核对，规则变更新对话加载。
用户启用 Obsidian 管理时，以 docs-root 为独立库根；用 project_view_refresh / info 生成或检查 agent/acceptance/ 视图和打开链接，保留批注与既有配置，不自动注册全局库，不把子任务通过当最终验收。
<!-- single-conversation-subagent end -->
