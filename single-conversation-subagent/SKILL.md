---
name: single-conversation-subagent
description: 单对话两级 Subagent 协作；首次选模式，开场确认验证、长任务 Codex 复核策略与动态收尾审查；支持原生提问、MCP 状态和文本回退。
---

主对话负责用户交互、阶段记忆、直接派发和最终验收。子 Agent 只分析、实现或测试，不再委派。MCP 记录交互、状态和证据，不调度 Agent。

## 开场

1. 先做足以推荐模式的少量只读调查。项目根用用户明确指定的目录，否则用当前对话工作目录，不上溯 Git 根。核对宿主技能列表：有 codex-review 传 `registered`，明确不可用传 `unregistered`，无法确认传 `unknown`。
2. `session_open` 传项目绝对路径、宿主真实 conversation_id（有才传）和已核实的 `review_registration`。项目元数据和索引可写时传 `register_obsidian=true`，建库并登记给 PyQt。已有选择直接沿用。保留 session_id 和原 client_name；同一会话重连或补登记时用 `resume_session_id` 和原项目根恢复。MCP 不可用时，沿用纯 Skill 流程。
3. 首次问快速处理 / 标准执行 / 长任务 / 只调查，按范围和风险推荐一个。任何首条任务在实现前先完成四选一，只有用户明确说“不用问，直接做”时跳过为快速。显式切换用 `interaction_reopen(explicit_user_change=true)` 后记录新选择。
4. **开始实现、执行测试或派发前完成开场确认**。标准/长任务先问验证范围并确认写入。
   - **长任务复核策略（codex_gate）**：验证范围确认后问一次 `interaction_request(kind="codex_gate", recommendation=["packages"])`。`packages` = 每个工作包由主对话启动 Codex 只读复核，并在收尾再审查（推荐）；`closure` = 仅收尾审查；`none` = 不做 Codex 审查。推荐不是确认，必须等 `CONFIRMED`。旧长会话没有 gate 时，首次创建工作包/更新或手动 `project_view_refresh` 返回 `INTAKE_REQUIRED`，补问一次即可，不回退旧 `codex_review`。
   - **标准/快速收尾审查（codex_review）**：保持原规则。codex-review 注册已确认时问一次 `interaction_request(kind="codex_review", recommendation=["yes"] 或 ["no"])`；只调查、未知或未注册跳过。长任务不再问 `codex_review`，MCP 返回 `SKIPPED/USE_CODEX_GATE`。
   - **Codex 方案指导（仅一次）**：如果即将进行的是高危操作（修改系统关键配置、删除或覆盖关键数据、不可逆的系统级操作等），或模式为长任务，在实现前推荐使用 Codex 做一次只读项目方案指导。用宿主原生问答（没有原生工具时文本回退）问一次；用户同意后，主对话运行一次 `codex exec -m gpt-6.2-sol -s read-only -C <项目根> -c model_reasoning_effort=high`，brief 写清目标、计划改动、高危点、约束、验证和回滚，读取结果纳入方案，并记录到项目协作/证据。同一任务只做一次，不重复；Codex 不可用或用户拒绝时说明并继续。只调查不触发；快速/标准且非高危跳过。
   - **收尾审查执行**：长任务 gate 为 `packages` 或 `closure`、或标准/快速 `codex_review` 为 `yes` 时，完成实现和所选验证后按 `codex-review` 执行一次；`none`/`no` 不执行。开场只记录选择。**方案指导与收尾审查是两件事，方案指导不替代收尾审查。**

## 逐题交互

- 原生路径（ZCode 默认）：`interaction_request(channel="native")` 返回 `NATIVE_QUESTION_REQUIRED` 时，立即调用宿主原生问答工具。用返回的简短 `display_text` 和 `options`，将推荐项置顶并标明。登记状态本身不证明已显示。等到用户实际回答，再用原 `question_id` 调 `interaction_submit`；**看到 `CONFIRMED` 才请求下一题**。没有原生工具时，使用同一题的文本回退。
- 文本路径：展示完整 `display_text`，结束当轮等待明确回复，然后用同一题号提交。每次只用一路提问。收到 `QUESTION_PENDING` 时处理返回的原题；如果用户已答就补提交，否则继续等待。需要重试已结束的题时，按用户明确要求重开。取消、拒绝、失败和默认勾选均不确认选择。
- MCP form 是配置为 mcp 或 auto 且支持表单时的回退。仅 `interaction_ask` 使用服务端 15 秒截止。按已告知的超时策略处理，`TIMED_OUT` 不写确认。无 form 或无回通道时用同一题号走原生或文本。原生/文本不声称 15 秒。详见 [MCP 用法](references/mcp.md)。
- 推荐理由一句话，最多 160 字。首次用 `workspace_scan` 辅助判断范围，不把扫描全文回传到每道题。上下文压缩时只保留会话 ID、项目根、已确认选择、待答题号、工作包修订、下一步和证据路径；按需读取详细状态。

## 长任务执行

所有任务性查找、分析、实现和测试都派执行子 Agent，主对话不直接做；主对话只保留目标、决策、未决问题、下一步、工作包修订、证据路径和协调记录，验收只核对关键证据。

- **三角色**：侦察者（`analyze`，只读）、实施者（`implement`，只改 `allowed_files`）、验证者（`test`，只跑命令）。职责、边界、返回 schema 和 persona 模板见 [roles.md](references/roles.md)。**Codex 技术复核者不是宿主子 Agent，只由主对话启动；子 Agent 不得启动 Codex，也不得再委派。**
- **扇出预算**：调查阶段有 n 个独立只读子任务时，起步并行 min(4, n) 个侦察者；实现阶段按 `allowed_files` 两两不相交取最大并行集合；默认并发上限 4，没有宿主真实并发证据时取 1。依赖、资源冲突和版本仍以 MCP 校验为准，同文件/同设备串行。
- **先占后派**：`work_attempt_prepare` 或单包批量的 `work_attempt_prepare_many` 成功后再启动宿主 Agent；批量全有或全无，同批依赖按波次准备。结果用 `work_attempt_update` / `work_attempt_update_many` 串行登记，`operation_id` 保证重试幂等。长执行可用 `work_attempt_note` 追加审计备注（不改版本、不续租、不证明存活），用 `run_map` 查看有界 JSON/Mermaid。
- **packages 包级复核**：工作包应声明一个 `review` 子任务。其他子任务完成且无活动 attempt 后，单独 prepare review attempt，主对话启动 Codex 只读复核；证据是严格 JSON manifest + 原始最终消息。ACCEPT 记 `PASS`，REJECT 记 `FAIL`，两者都可落账，只有 ACCEPT 能放行 `REVIEW_READY`。复核窗口内禁止其他 attempt；文件、定义或非 review 结果变化即 `REVIEW_STALE`，用新 review attempt 重审。`review` 结果不能用 `work_package_update(results=...)` 直接写。
- **静止与终态**：有活动 attempt 时不得 `REVIEW_READY`、`COMPLETED`、`ABANDONED`、切修订或 handoff。验收通过用 `COMPLETED`、重复或被取代用 `ABANDONED`，两类终态都必须写 `close_reason`，关闭后不可修改。

工作包、并发、证据和交接详见 [完整协议](references/protocol.md)。

## 破坏性操作闸门（全模式）

删除/覆盖用户数据或系统关键配置、擦除/烧录、drop 数据库/表、不可逆 git 操作、递归 `chmod`/`chown`、卸载关键包、`allowed_files` 之外的大范围改写等，必须走闸门：主对话准备命令/`cwd`/目标/影响面/备份/回滚 →（需要时）Codex 只读预审 → `operation_gate_record` → 执行前 `operation_gate_check` 为 `APPROVED`，并保留用户独立确认。ACCEPT 需报告唯一结论 + `user_confirmed=true`；REJECT 对同 `operation_id`/digest 是终态，不可覆盖；Codex 不可用只能记 `UNAVAILABLE_OVERRIDE`，不得伪记 ACCEPT。MCP 不执行、不拦截宿主命令；没有宿主 Hook 时这是协议级约束（`PROTOCOL_ONLY`），不是执行级强制。普通代码编辑、证据写入和视图刷新不触发。详见 [完整协议](references/protocol.md)。

## 执行与收尾

快速由主对话直接处理；只调查保持只读，范围大时可派只读子 Agent。标准执行由主对话按计划实现和验证，按需建工作包与证据，收尾按 `codex_review` 选择执行审查。长任务按上节三角色与复核策略执行。子 Agent 继承主对话的授权和验证范围。未选的测试标 `NOT_RUN`；不做测试与只记录方法互斥，均不标 PASS。**高危/长任务的 Codex 方案指导只在开工前做一次；实施中范围根本变化按新任务处理或先向用户确认，不反复启动方案指导。** 不贴全量日志，主对话核对关键证据后验收。

docs-root 优先 00_doc / 00_docs / docs / doc，均无则用 docs。项目与协作记忆、证据在其 agent/，审查报告在 04_test/report/。MCP 初始化缺失记录，主对话维护审查记录；恢复前核对 SQLite 与手工记录。PyQt 可直接打开已登记的库，视图刷新和故障处理见 [Obsidian 视图](references/obsidian.md)。
