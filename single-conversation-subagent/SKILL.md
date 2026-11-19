---
name: single-conversation-subagent
description: 单对话两级 Subagent 协作；首次选模式，开场确认验证、高危/长任务一次 Codex 方案指导与动态收尾审查；支持原生提问、MCP 状态和文本回退。
---

主对话负责用户交互、阶段记忆、直接派发和最终验收。子 Agent 只分析、实现或测试，不再委派。MCP 记录交互、状态和证据，不调度 Agent。

## 开场

1. 先做足以推荐模式的少量只读调查。项目根用用户明确指定的目录，否则用当前对话工作目录，不上溯 Git 根。核对宿主技能列表：有 codex-review 传 `registered`，明确不可用传 `unregistered`，无法确认传 `unknown`。
2. `session_open` 传项目绝对路径、宿主真实 conversation_id（有才传）和已核实的 `review_registration`。项目元数据和索引可写时传 `register_obsidian=true`，建库并登记给 PyQt。已有选择直接沿用。保留 session_id 和原 client_name；同一会话重连或补登记时用 `resume_session_id` 和原项目根恢复。MCP 不可用时，沿用纯 Skill 流程。
3. 首次问快速处理 / 标准执行 / 长任务 / 只调查，按范围和风险推荐一个。任何首条任务在实现前先完成四选一，只有用户明确说“不用问，直接做”时跳过为快速。显式切换用 `interaction_reopen(explicit_user_change=true)` 后记录新选择。
4. **开始实现、执行测试或派发前完成开场确认**。标准/长任务先问验证范围并确认写入。
   - **Codex 方案指导（仅一次）**：如果即将进行的是高危操作（修改系统关键配置、删除或覆盖关键数据、不可逆的系统级操作等），或模式为长任务，在实现前推荐使用 Codex 做一次只读项目方案指导。用宿主原生问答（没有原生工具时文本回退）问一次；用户同意后，主对话运行一次 `codex exec -m gpt-6.1-sol -s read-only -C <项目根> -c model_reasoning_effort=high`，brief 写清目标、计划改动、高危点、约束、验证和回滚，读取结果纳入方案，并记录到项目协作/证据。同一任务只做一次，不重复；Codex 不可用或用户拒绝时说明并继续。只调查不触发；快速/标准且非高危跳过。
   - **收尾审查**：随后按项目情况给一个推荐：高风险（系统关键配置、删除关键数据、安全相关）、跨仓库或大范围改动、验证不足，或方案指导发现重大风险时推荐使用；大多数普通/小型/已验证任务推荐不使用。用 `interaction_request(kind="codex_review", recommendation=["yes"] 或 ["no"])` 记录；只调查或 review_registration 未确认时跳过。开场只记录选择，完成实现和所选验证后再按选择运行一次只读审查。**方案指导与收尾审查是两件事，方案指导不替代收尾审查。**

## 逐题交互

- 原生路径（ZCode 默认）：`interaction_request(channel="native")` 返回 `NATIVE_QUESTION_REQUIRED` 时，立即调用宿主原生问答工具。用返回的简短 `display_text` 和 `options`，将推荐项置顶并标明。登记状态本身不证明已显示。等到用户实际回答，再用原 `question_id` 调 `interaction_submit`；**看到 `CONFIRMED` 才请求下一题**。没有原生工具时，使用同一题的文本回退。
- 文本路径：展示完整 `display_text`，结束当轮等待明确回复，然后用同一题号提交。每次只用一路提问。收到 `QUESTION_PENDING` 时处理返回的原题；如果用户已答就补提交，否则继续等待。需要重试已结束的题时，按用户明确要求重开。取消、拒绝、失败和默认勾选均不确认选择。
- MCP form 是配置为 mcp 或 auto 且支持表单时的回退。仅 `interaction_ask` 使用服务端 15 秒截止。按已告知的超时策略处理，`TIMED_OUT` 不写确认。无 form 或无回通道时用同一题号走原生或文本。原生/文本不声称 15 秒。详见 [MCP 用法](references/mcp.md)。
- 推荐理由一句话，最多 160 字。首次用 `workspace_scan` 辅助判断范围，不把扫描全文回传到每道题。上下文压缩时只保留会话 ID、项目根、已确认选择、待答题号、工作包修订、下一步和证据路径；按需读取详细状态。

## 执行与收尾

快速直接处理；只调查保持只读，范围大时可派只读子 Agent。**长任务下所有任务性查找、分析、实现和测试都派执行子 Agent，主对话不直接做**；主对话只保留目标、决策、未决问题、下一步、工作包修订、证据路径和协调记录，验收时只核对关键证据。工作包验收通过用 `COMPLETED` 关闭；重复、被取代或不再需要的用 `ABANDONED` 关闭，两类终态都必须写 `close_reason`，关闭后不可修改。长任务可并发多个执行子 Agent：先 `work_attempt_prepare` 占资源再派发，按完成收割，结果用 `work_attempt_update` 串行登记；有活动 attempt 时不得 `REVIEW_READY`、关闭或切修订。子 Agent 继承主对话的授权和验证范围。未选的测试标 `NOT_RUN`；不做测试与只记录方法互斥，均不标 PASS；危险操作独立确认。**高危/长任务的 Codex 方案指导只在开工前做一次；实施中范围根本变化按新任务处理或先向用户确认，不反复启动方案指导。** 工作包、并发、证据和交接按需查 [完整协议](references/protocol.md)。不贴全量日志，主对话核对关键证据后验收。

docs-root 优先 00_doc / 00_docs / docs / doc，均无则用 docs。项目与协作记忆、证据在其 agent/，审查报告在 04_test/report/。MCP 初始化缺失记录，主对话维护审查记录；恢复前核对 SQLite 与手工记录。PyQt 可直接打开已登记的库，视图刷新和故障处理见 [Obsidian 视图](references/obsidian.md)。
