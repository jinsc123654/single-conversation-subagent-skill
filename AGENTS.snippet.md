<!-- single-conversation-subagent begin -->
项目根优先用用户明确指定的目录，否则用当前对话工作目录。按 Skill 调用 session_open(project_root=<绝对项目根>, register_obsidian=true) 建库并登记给 PyQt。MCP 工作区配置只决定目录授权范围；登记独立于工作模式和验证范围。首次 session_open 前核对当前会话的 codex-review 可用性；补登记或重连用原 session_id 和项目绝对路径恢复，细节见 Skill。
首次任务四选一，先做少量只读调查，推荐一个模式。只有用户明确要求“不用问，直接做”才跳过。由宿主逐题询问并提交真实回复，MCP 返回 CONFIRMED 后才进入下一题。标准/长任务在开工前确认验证范围；长任务随后确认 codex_gate（none|closure|packages，推荐 packages），标准/快速保持原 codex_review yes/no，长任务不再问 codex_review。高危操作或长任务开工前推荐一次 Codex 只读方案指导（仅一次），用户确认后主对话运行并记录，不重复，方案指导不替代收尾审查。
长任务派三类执行子 Agent：侦察者（analyze，只读）、实施者（implement，只改 allowed_files）、验证者（test，只跑命令）；Codex 技术复核者不是子 Agent，只由主对话启动。调查阶段有 n 个独立只读子任务时起步 min(4, n) 个只读子 Agent；实现按 allowed_files 两两不相交并行，默认上限 4，无宿主并发证据取 1。packages 包在 REVIEW_READY 前由主对话完成 Codex 复核并记录 ACCEPT；复核窗口内禁止其他 attempt，文件/定义/结果变化即 STALE。先 prepare 再派发，结果用 attempt update 串行登记；有活动 attempt 时不得 REVIEW_READY、COMPLETED、ABANDONED 或切修订。
破坏性操作在所有模式都要先 operation_gate_record、执行前 operation_gate_check 为 APPROVED，并保留用户独立确认；ACCEPT 需报告唯一结论 + 用户确认，REJECT 对同 operation_id/digest 是终态，Codex 不可用只能记 UNAVAILABLE_OVERRIDE，不得伪记 ACCEPT；无宿主 Hook 时是协议级约束。工作包验收通过用 COMPLETED、重复或被取代用 ABANDONED 关闭并写原因；终态不可修改。已确认选择直接沿用。收到 QUESTION_PENDING 时按返回的原题号处理。纯 Skill 回退与细节见 SKILL.md 及其 references/。
docs-root 依次 00_doc/00_docs/docs/doc，均无建 docs；记忆与证据在其 agent/，WP 审查报告 04_test/report/。不迁移旧记录，SQLite 与手工 Markdown 恢复时显式核对，规则变更新对话加载。
用户启用 Obsidian 管理时，以 docs-root 为独立库根；用 project_view_refresh / info 生成或检查 agent/acceptance/ 视图和打开链接，保留批注与既有配置，不自动注册全局库，不把子任务通过当最终验收。
<!-- single-conversation-subagent end -->
