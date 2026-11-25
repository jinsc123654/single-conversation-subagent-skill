---
name: single-conversation-subagent
description: 单对话两级 Subagent 协作；按模式确认验证和复核，ZCode 长任务由方案顾问指导，使用注册 Agent 并行执行，由固定审阅员独立审查。
---

主对话负责用户交互、阶段记忆、直接派发和最终验收。子 Agent 只分析、实现或测试，不再委派。MCP 记录交互、状态和证据，不调度 Agent。

**ZCode 使用宿主注册身份。** 侦察、实施、验证分别派 `查询侦察员`、`实施工程师`、`测试验证员`；正式审查只派 `固定审阅员`。用 `Agent(subagent_type=<准确角色名>)`，沿用角色的模型和权限。下文把它们称为执行角色和审阅员；其他宿主仍用各自的执行角色和 `codex-review`。ZCode 启用 `SCS_NATIVE_REVIEW=1`。

## 开场

1. 项目根用用户明确指定的目录，否则用当前对话工作目录。模式和根已明确就开始会话绑定；只有缺少信息时，做至多一次直接相关的只读查询。开场确认以前先不做任务排查、构建、串口或设备操作。ZCode 从宿主角色注册表核对 `固定审阅员`；其他宿主核对 `codex-review`。有则 `registered`，明确无则 `unregistered`，未知则 `unknown`。
2. `session_open` 传项目绝对路径、宿主真实 conversation_id（有才传）和已核实的 `review_registration`。首次用户只说「不用问，直接做」时，附 `explicit_skip=true` 进入快速模式。项目元数据和索引可写时传 `register_obsidian=true`，建库并登记给 PyQt。已有选择直接沿用。保留 session_id 和原 client_name；同一会话重连或补登记时用 `resume_session_id` 和原项目根恢复。ZCode 核对返回 `native_review=true`；服务未启用时先说明配置，普通角色仍可按宿主权限使用，正式审查按原生方式手工记录。MCP 不可用时用原生问答或文本确认，主对话保留证据。
3. 模式未定时问快速处理 / 标准执行 / 长任务 / 只调查，推荐一个。用户已明确选模式时，以 `interaction_request(kind="mode", channel="text")` 和真实选择 `interaction_submit` 直接登记。沿用已确认模式，显式切换用 `interaction_reopen(explicit_user_change=true)`。
4. **开始实现、执行测试或派发前完成开场确认**。标准/长任务先问验证范围并确认写入。
   - **长任务确认固定审阅员的审查时机**。验证范围后问一次「包级复核 + 收尾审查」「仅收尾审查」「不做独立复核」。用 `codex_gate` 提交对应的 `packages`、`closure`、`none`。固定审阅员不可用时说明可用范围，允许选择不做独立复核。已确认的复核要求要实际完成。
   - **标准或快速确认收尾是否请固定审阅员**。核实审阅员已注册后提问，使用 `codex_review` 提交 `yes` 或 `no`。只调查、审阅员可用性未知或未注册时跳过。
   - **长任务必做方案指导**：ZCode 确认长任务、验证范围和复核策略后，先由主对话调用一次 `Agent(subagent_type="方案顾问")`。选择长任务即进入这项只读指导，无须再弹一个同意框。简报包含目标、允许范围、候选方案、风险、验证和回滚。等实际完成回执，将建议与需要用户决策的事项纳入协调记录，再拆工作包和派执行角色。同一任务继续或恢复时沿用已交回的方案指导；角色不可用或未完成时，报告受阻并先解决缺项。选择「不做独立复核」的长任务也要派方案顾问。
   - **高危短任务方案指导**：用户同意后可派一次 `方案顾问`，高危操作仍另走预审和确认。其他宿主沿用原有 Codex 方案指导规则。方案意见、操作预审与正式审查分别保存。
   - **收尾审查**：所选策略要求审查时，实现和验证完成后独立派一次审阅员。ZCode 新开 `Agent(subagent_type="固定审阅员")`，只读核对范围、证据和现状，保存宿主返回的实际 Agent ID 和原始最终消息。只有具有唯一 `结论: ACCEPT` 的实际完成回执才能报告通过。

## 逐题交互

- 原生路径（ZCode 默认）：`interaction_request(channel="native")` 返回 `NATIVE_QUESTION_REQUIRED` 时，立即调用宿主原生问答工具。用返回的简短 `display_text` 和 `options.label` 提问，将推荐项置顶并标明。提交时用对应的 `options.value`。登记状态本身不证明已显示。等到用户实际回答，再用原 `question_id` 调 `interaction_submit`；**看到 `CONFIRMED` 才请求下一题**。没有原生工具时，使用同一题的文本回退。
- 文本路径：展示完整 `display_text`，结束当轮等待明确回复，然后用同一题号提交。每次只用一路提问。收到 `QUESTION_PENDING` 时处理返回的原题；如果用户已答就补提交，否则继续等待。需要重试已结束的题时，按用户明确要求重开。取消、拒绝、失败和默认勾选均不确认选择。
- MCP form 是配置为 mcp 或 auto 且支持表单时的回退。仅 `interaction_ask` 使用服务端 15 秒截止。按已告知的超时策略处理，`TIMED_OUT` 不写确认。无 form 或无回通道时用同一题号走原生或文本。原生/文本不声称 15 秒。详见 [MCP 用法](references/mcp.md)。
- 推荐理由一句话，最多 160 字。首次用 `workspace_scan` 辅助判断范围，不把扫描全文回传到每道题。上下文压缩时只保留会话 ID、项目根、已确认选择、待答题号、工作包修订、下一步和证据路径；按需读取详细状态。

## 长任务执行

所有任务性查找、分析、实现和测试都派执行子 Agent，主对话不直接做；主对话只保留目标、决策、未决问题、下一步、工作包修订、证据路径和协调记录，验收只核对关键证据。

- **三角色**：侦察者（`analyze`，只读）、实施者（`implement`，只改 `allowed_files`）、验证者（`test`，只跑命令）。职责、边界、返回 schema 和 persona 模板见 [roles.md](references/roles.md)。ZCode 主对话按开头的注册身份派单。正式审阅由主对话单独派发，子 Agent 不得再委派或自行发起正式审阅。
- **扇出预算**：调查阶段有 n 项独立任务时，起步并行 min(4, n) 个 `查询侦察员`；实施阶段用多个 `实施工程师` 处理不相交的文件；独立验证用 `测试验证员`。按依赖分波次，并发上限 4，宿主不支持并行时串行。共享串口、板卡、写范围和其他独占资源串行。ZCode 在同一批次发送实际 Agent 调用，记录各自的宿主 ID。
- **先占后派**：`work_attempt_prepare` 或单包批量的 `work_attempt_prepare_many` 成功后再启动宿主 Agent；批量全有或全无，同批依赖按波次准备。结果用 `work_attempt_update` / `work_attempt_update_many` 串行登记，`operation_id` 保证重试幂等。长执行可用 `work_attempt_note` 追加审计备注（不改版本、不续租、不证明存活），用 `run_map` 查看有界 JSON/Mermaid。
- **packages 包级复核**：工作包应声明一个 `review` 子任务。其他子任务完成且无活动 attempt 后，单独 prepare review attempt。ZCode 依返回的 `agent_type` 独立派 `固定审阅员`，传 `review_subject`、有界 diff 和验证证据；保存原始最终消息和 [原生 v2 manifest](references/mcp.md#zcode-固定审阅员回执)，以真实 `host_agent_id` 结束 attempt。其他宿主仍用 Codex 原有 manifest。ACCEPT 记 `PASS`，REJECT 记 `FAIL`，两者都可落账，只有 ACCEPT 能放行 `REVIEW_READY`。复核窗口内禁止其他 attempt；文件、定义或非 review 结果变化即 `REVIEW_STALE`，用新 review attempt 重审。`review` 结果不能用 `work_package_update(results=...)` 直接写。
- **静止与终态**：有活动 attempt 时不得 `REVIEW_READY`、`COMPLETED`、`ABANDONED`、切修订或 handoff。验收通过用 `COMPLETED`、重复或被取代用 `ABANDONED`，两类终态都必须写 `close_reason`，关闭后不可修改。

工作包、并发、证据和交接详见 [完整协议](references/protocol.md)。

## 破坏性操作闸门（全模式）

删除/覆盖用户数据或系统关键配置、擦除/烧录、drop 数据库/表、不可逆 git 操作、递归 `chmod`/`chown`、卸载关键包、`allowed_files` 之外的大范围改写等，必须走闸门：主对话准备命令/`cwd`/目标/影响面/备份/回滚 → 审阅员只读预审 → `operation_gate_record` → 执行前 `operation_gate_check` 为 `APPROVED`，并保留用户独立确认。ACCEPT 需报告唯一结论 + `user_confirmed=true`；ZCode 先用实际参数 `operation_gate_check` 取得 `operation_digest`，派 `固定审阅员`，登记时提交对应的 v2 `review_receipt`；REJECT 对同 `operation_id`/digest 是终态，不可覆盖；审阅员不可用只能记 `UNAVAILABLE_OVERRIDE`，不得伪记 ACCEPT。MCP 不执行、不拦截宿主命令；没有宿主 Hook 时这是协议级约束（`PROTOCOL_ONLY`），不是执行级强制。普通代码编辑、证据写入和视图刷新不触发。详见 [完整协议](references/protocol.md)。

## ZCode 子 Agent 上下文判断

用户问子 Agent 上下文占用，或主对话准备 `SendMessage` 续跑已结束的子 Agent 时，由主对话查宿主记录：先调用 `ListModels`，再运行 [只读上下文探针](references/zcode-context.md)，用目标子会话最近一次请求的用量和该子 Agent 实际 `provider/model` 的窗口计算。不要借用主对话模型窗口，也不要按轮数、累计账单或缓存未命中数估算。

只有原工作线、角色、基线和授权仍相容，且探针给出 `REUSE`，才允许考虑续跑；长任务仍先 prepare 新 attempt。`ROTATE` 或 `UNKNOWN` 时不续旧实例，需要继续就新开同角色，只带必要交接。包级复核、正式收尾和操作预审仍独立新开 `固定审阅员`，不受占用比例影响。

## 执行与收尾

快速由主对话直接处理；只调查保持只读，范围大时可派只读子 Agent。标准执行由主对话按计划实现和验证，按需建工作包与证据，收尾按已确认的选择派审阅员。长任务按上节三角色与复核策略执行。子 Agent 继承主对话的授权和验证范围。未选的测试标 `NOT_RUN`；不做测试与只记录方法互斥，均不标 PASS。主对话核对关键证据后验收，省略全量日志。

docs-root 优先 00_doc / 00_docs / docs / doc，均无则用 docs。项目与协作记忆、证据在其 agent/，审查报告在 04_test/report/。MCP 初始化缺失记录，主对话维护审查记录；恢复前核对 SQLite 与手工记录。PyQt 可直接打开已登记的库，视图刷新和故障处理见 [Obsidian 视图](references/obsidian.md)。
