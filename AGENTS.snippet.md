<!-- single-conversation-subagent begin -->
每个真实对话首个任务先读 single-conversation-subagent Skill 并四选一：快速处理/标准执行/长任务/只调查。必要只读调查后只标一个动态推荐；后续沿用本对话选择；仅明确“不用问，直接做”可跳过。原生提问可用时真实调用优先，其次可选 MCP interaction_request，最后完整文本选项并结束当轮等待明确回复。推荐不等于确认；取消、工具不可见、RPC 失败或没有弹窗均不授权默认执行。不得用命令等待推断用户超时。同一时刻不重复打开两路提问。
标准/长任务首次确认验证范围；只有明确注册 codex-review 才追加收尾使用/不使用，唯一推荐不使用。明确已有授权沿用；不做测试/只记录方法互斥且排除测试，标 NOT_RUN；破坏性操作另确认。快速不建子 Agent；只调查只读。主对话直接协调和验收，原生执行子 Agent 不再委派；MCP 不调度 Agent。MCP 不可用时纯 Skill 仍可工作，不擅用推荐项。
docs-root 依次 00_doc/00_docs/docs/doc，均无建 docs；记忆与证据在其 agent/，WP 审查报告 04_test/report/。不迁移旧记录，SQLite 与手工 Markdown 恢复时显式核对，规则变更新对话加载。
用户启用 Obsidian 管理时，以 docs-root 为独立库根；用 project_view_refresh / info 生成或检查 agent/acceptance/ 视图和打开链接，保留批注与既有配置，不自动注册全局库，不把子任务通过当最终验收。
<!-- single-conversation-subagent end -->
