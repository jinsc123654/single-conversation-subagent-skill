<!-- single-conversation-subagent begin -->
每个真实对话首个任务先读 single-conversation-subagent Skill 并四选一：快速处理/标准执行/长任务/只调查。四选一前先做一次任务相关只读调查（目标目录/README/git status/少量文件，足够判断范围；不要只列目录就提问），不检查 MCP 注册/配置/项目根，不读无关记忆，不做长搜索；调查在提问前完成，调查后只调用一次 session_open（可省 project_root/client_name，按 host 命名空间）或原生/文本提问，带 analysis 说明发现与推荐；提问后等待，不再调查。按 session_open 返回的 question_channel 路由：native→原生；text→文本；mcp→interaction_ask(timeout_seconds=15)，fallback 用同一 question_id 直接 interaction_submit；auto→可用则 mcp，否则原生/文本。原生/文本路径不声称 15s。推荐不等于确认；取消、工具不可见、RPC 失败或没有弹窗均不授权默认执行。不得用命令等待推断用户超时。ZCode 原生自动解析 60s 隐藏 + 5min 截止，不是 15s。同一时刻不重复打开两路提问。
标准/长任务首次确认验证范围；只有明确注册 codex-review 才追加收尾使用/不使用，唯一推荐不使用。明确已有授权沿用；不做测试/只记录方法互斥且排除测试，标 NOT_RUN；破坏性操作另确认。快速不建子 Agent；只调查只读。worker 继承已确认 mode/verification，不重新四选一、不提问。主对话直接协调和验收，原生执行子 Agent 不再委派；MCP 不调度 Agent。MCP 不可用时纯 Skill 仍可工作，不擅用推荐项。
docs-root 依次 00_doc/00_docs/docs/doc，均无建 docs；记忆与证据在其 agent/，WP 审查报告 04_test/report/。不迁移旧记录，SQLite 与手工 Markdown 恢复时显式核对，规则变更新对话加载。
用户启用 Obsidian 管理时，以 docs-root 为独立库根；用 project_view_refresh / info 生成或检查 agent/acceptance/ 视图和打开链接，保留批注与既有配置，不自动注册全局库，不把子任务通过当最终验收。
<!-- single-conversation-subagent end -->
