<!-- single-conversation-subagent begin -->
项目根优先用用户明确指定的目录，否则用当前对话工作目录。按 Skill 调用 session_open(project_root=<绝对项目根>, register_obsidian=true) 建库并登记给 PyQt。MCP 工作区配置只决定目录授权范围；登记独立于工作模式和验证范围。首次 session_open 前核对当前会话的 codex-review 可用性；补登记或重连用原 session_id 和项目绝对路径恢复，细节见 Skill。
首次任务四选一，先做少量只读调查，推荐一个模式。只有用户明确要求“不用问，直接做”才跳过。由宿主逐题询问并提交真实回复，MCP 返回 CONFIRMED 后才进入下一题。标准/长任务在开工前确认验证范围，然后询问收尾 Codex 审查；快速模式在模式确认后问审查，只调查跳过。长任务下所有任务性查找、分析、实现和测试派执行子 Agent，主对话只保留目标、决策、未决问题、下一步、证据路径和记录。任务开始只记录审查选择，完成执行和已选验证后才运行审查。工作包验收通过用 COMPLETED、重复或被取代用 ABANDONED 关闭并写原因；终态不可修改。已确认选择直接沿用。收到 QUESTION_PENDING 时按返回的原题号处理，不另开问题。纯 Skill 回退及各种提问路径见 SKILL.md。
docs-root 依次 00_doc/00_docs/docs/doc，均无建 docs；记忆与证据在其 agent/，WP 审查报告 04_test/report/。不迁移旧记录，SQLite 与手工 Markdown 恢复时显式核对，规则变更新对话加载。
用户启用 Obsidian 管理时，以 docs-root 为独立库根；用 project_view_refresh / info 生成或检查 agent/acceptance/ 视图和打开链接，保留批注与既有配置，不自动注册全局库，不把子任务通过当最终验收。
<!-- single-conversation-subagent end -->
