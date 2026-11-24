---
name: single-conversation-subagent
description: ZCode 单对话协作；首次选模式，仅长任务自动编排专用 Agent 和原生复核；支持原生提问、MCP 状态和文本回退。
---

主对话负责用户交互、阶段记忆、`Agent` 派发、证据持久化和最终验收；子 Agent 不再委派。MCP 记录交互、状态和证据，不调度 Agent。ZCode stdio 服务配置 `SCS_NATIVE_REVIEW=1`；只使用当前宿主的原生 Agent，不启动外部 Codex，不加载外部复核技能或工具。

## 开场

1. 先做足以推荐模式的少量只读调查。项目根用用户明确指定的目录，否则用当前对话工作目录，不上溯 Git 根。核对当前 ZCode `Agent` 可派发的 `固定审阅员` / `继承审阅员`：至少一个已确认可用才传 `registered`，明确都不可用传 `unregistered`，不能核实传 `unknown`。`方案顾问` 的可派发性不计入复核登记；角色定义文件、已装外部技能也不能证明当前宿主可派发。
2. `session_open` 传项目绝对路径、宿主真实 conversation_id（有才传）和已核实的 `review_registration`。项目元数据和索引可写时传 `register_obsidian=true`，建库并登记给 PyQt。核对返回的 `native_review` 已启用；已有选择直接沿用。保留 session_id 和原 client_name；同一会话重连或补登记时用 `resume_session_id` 和原项目根恢复。MCP 不可用或未启用原生复核时按下文纯 Skill 回退，不提交旧式复核证据。
3. 首次问快速处理 / 标准执行 / 长任务 / 只调查，按范围和风险推荐一个。任何首条任务在实现前先完成四选一，只有用户明确说“不用问，直接做”时跳过为快速。只在用户明确要求切换时才用 `interaction_reopen(explicit_user_change=true)` 后记录新选择；不自动升为长任务。
4. **开始实现、执行测试或派发前完成适用的开场确认**。标准/长任务先问验证范围并确认写入；快速/只调查不问验证范围。只有长任务进入下面的专用角色流程。标准/快速/只调查不发起 SCS 的 `codex_review`、`codex_gate`、`review_route` 或方案指导问题，不因已登记角色、历史选择或旧 MCP intake 自动补问或派发。
   - **长任务复核时机（`codex_gate`）**：验证范围后问一次。`packages` = 每包复核且收尾再审（有可用角色时推荐）；`closure` = 只在收尾审；`none` = 不做包级或收尾审查。角色不可用时说明并推荐 `none`，不把 `closure` 当替代。推荐不算确认。旧长会话无 gate 时，首次工作包操作或手动 `project_view_refresh` 返回 `INTAKE_REQUIRED`，补问一次，不从兼容字段 `codex_review` 推断；新流程不问该旧题。
   - **长任务复核路由（`review_route`）**：gate 为 `packages`/`closure` 时，在派发前用 `interaction_request(kind="review_route", channel="native")` 确认 `gpt`（派发 `固定审阅员`，固定 GPT）或 `inherit`（派发 `继承审阅员`，`model: inherit`）。两者可用时推荐 `gpt`，但不默认确认；现有路由沿用，不覆盖模型、不自动切换。已选路由不可用时说明并阻塞受影响步骤，等待用户显式改选或恢复角色。
   - **长任务只读方案指导（可选，仅一次）**：开工前推荐一次，另用宿主原生问答取得同意（无工具则文本）；brief 包括目标、计划改动、风险、约束、验证和回滚。由主对话单独调用 `Agent(subagent_type="方案顾问")`（固定 GPT），只给建议、不出正式判定，不参与包级、收尾或操作预审，也不依赖或改变 `review_route`。主对话记录原始结果并纳入方案；拒绝或不可用则说明，不反复询问、不改派正式审阅员，也不解除其他复核或操作闸门。
   - **长任务收尾审查**：按 `codex_gate` 选择，实现和所选验证完成后由主对话派同一已确认路由一次；开场只记录选择。`none` 不审。未实际完成、缺证据或 REJECT 均不能报告审查通过。

## 逐题交互

- ZCode 默认使用原生提问。`interaction_request(channel="native")` 返回 `NATIVE_QUESTION_REQUIRED` 时，调用宿主原生问答，展示 `display_text` 和 `options`，推荐置顶并标明。等真实回复后用原 `question_id` 调 `interaction_submit`；**看到 `CONFIRMED` 才请求下一题**。没有原生问答工具时，用同一题走文本回退。
- 文本路径：展示完整 `display_text`，结束当轮等待明确回复，再用同一题号提交。每题只用一路；`QUESTION_PENDING` 的适用题按返回的原题处理。标准/快速/只调查若遇到旧 MCP 未决复核题，不展示、不代填，按下文兼容处理。取消、拒绝、失败、超时和默认推荐均不确认选择；需要重试终态题时按用户明确要求重开。
- MCP form 只用于配置为 mcp 或 auto 且支持表单的回退；只有 `interaction_ask` 声明服务端 15 秒。原生/文本不套用该期限。详见 [MCP 用法](references/mcp.md)。
- 推荐理由一句话，最多 160 字。首次用 `workspace_scan` 辅助判断，不在每题复述。压缩时保留会话 ID、根、确认选择、待答题号、工作包修订、下一步和证据路径。

## 长任务执行

任务性查找、分析、实现和测试都派执行子 Agent；主对话仅协调、制备有界复核输入、持久化证据并核对关键证据。

- **三执行角色**：`analyze` → `Agent(subagent_type="查询侦察员")`（固定 DeepSeek，只读）；`implement` → `Agent(subagent_type="实施工程师")`（继承模型，只改 `allowed_files`）；`test` → `Agent(subagent_type="测试验证员")`（固定 DeepSeek，只运行指定验证命令）。不传模型覆盖。授权、提示模板和返回格式见 [角色](references/roles.md)。没有真实原生角色就标 `BLOCKED`，不以通用 Agent 假冒、不静默改为主对话执行。
- **扇出预算**：n 个独立只读子任务起步 min(4, n) 个 `查询侦察员`；实现按 `allowed_files` 两两不相交取最大并行集合。默认上限 4，无宿主并发证据取 1。依赖、资源和版本以 MCP 校验为准；同文件/设备串行。
- **先占后派**：`work_attempt_prepare` 或单包原子 `work_attempt_prepare_many` 成功后才派 Agent，依赖分波次。由主对话串行 `work_attempt_update` / `work_attempt_update_many` 登记结果，用 `operation_id` 幂等重试。`work_attempt_note` 不改版本、不续租、不证明存活；`run_map` 查看有界 JSON/Mermaid。
- **后台等待**：保留宿主任务 ID 与 attempt 的对应关系，优先接收完成通知。先推进独立工作；依赖结果时保持等待。经过合理间隔仍未收到通知，或会话恢复后，用原任务 ID 做一次非阻塞状态核对。若仍在运行，隔一段时间再查，只在状态有实质变化时更新用户。查询超时仍按等待处理；只有查不到可靠状态才记 `UNKNOWN`，不得据此结束 attempt、释放资源或重派。详见 [调度](references/protocol.md#调度)。
- **包级复核**：`packages` 包声明一个 `review` 子任务。其余子任务完成且无活动 attempt 后，单独 prepare 并按 `input_snapshot` 锁定的 `review_route` 派只读 `固定审阅员`（`gpt`）或 `继承审阅员`（`inherit`）。主对话准备有界 diff/哈希和证据索引，保存 Agent 原始最终消息及 [原生 v2 manifest](references/mcp.md#review-子任务与证据)，以真实 `host_agent_id` 单独 `ENDED`。ACCEPT 记 `PASS`，REJECT 记 `FAIL`；只有当前 subject 的 ACCEPT 放行。复核窗口禁止其他 attempt；文件、定义或非 review 结果变化即 `REVIEW_STALE`，用新 attempt 重审。不能用 `work_package_update(results=...)` 直接写 `review`。
- **静止与终态**：有活动 attempt 时不得 `REVIEW_READY`、`COMPLETED`、`ABANDONED`、切修订或 handoff。验收通过用 `COMPLETED`，重复/被取代用 `ABANDONED`；终态必须写 `close_reason`，且不可修改。

## 破坏性操作闸门（全模式）

删除/覆盖用户数据或系统关键配置、擦除/烧录、drop 数据库/表、不可逆 git 操作、递归 `chmod`/`chown`、卸载关键包、`allowed_files` 外大范围改写等均需闸门。标准/快速如遇此类操作，立即阻塞该操作并说明原生预审须由用户明确切换到长任务后才能启动；不自动切换，不在短模式补问路由，不用内建/手动角色或历史批准绕过。只调查不执行此类操作。

长任务中，主对话冻结命令、`cwd`、目标、影响、备份、回滚和基线，准备有界输入和 `operation_digest`；由确认路由的原生 Reviewer 只读预审（无路由时单独确认，即使 `codex_gate=none` 也不免除），主对话落证据并 `operation_gate_record`。执行前以实际参数 `operation_gate_check`；只有 `APPROVED` 且保留用户独立确认才执行。ACCEPT 需当前 digest 的原生证据、唯一结论及 `user_confirmed=true`；REJECT 对同 `operation_id`/digest 是终态。复核不可用只能如实记 `UNAVAILABLE_OVERRIDE`（用户确认及理由），不是放行。MCP 不执行或拦截命令；无宿主 Hook 时是协议级约束。普通代码编辑、证据写入、视图刷新不触发。详见 [完整协议](references/protocol.md#破坏性操作闸门)。

## 执行、收尾与纯 Skill 回退

- **快速/标准**：回到 ZCode 内建执行流程，由主对话直接处理，只在有用时使用默认/内建 Agent；标准仍须先确认验证范围。无需 SCS 工作包或专用角色，也不跑 SCS 收尾审查。
- **只调查**：保持只读，按需用 ZCode 内建 `Explore`，不自动派 `查询侦察员`。
- **长任务**：按长任务章节委派。

**手动调用**：仅用户明确点名时，可在自动流程外手动调用可用的专用角色；仍遵守当前模式的读写与授权限制，不因此切换模式、启动 SCS 复核问答或替代正式复核/闸门。

未选测试标 `NOT_RUN`；不做测试与只记录方法互斥，均不标 PASS。不贴全量日志；主对话核对证据后验收，复核 ACCEPT 不等于最终验收。

**旧 MCP 兼容**：短模式的可选工作包、手动 `project_view_refresh` 等若被 `INTAKE_REQUIRED` 的旧复核缺项阻塞，说明并暂缓该 SCS 操作，不代填、不修改记录、不关闭原生保护；已有视图可用只读 `project_view_info`。若旧未决复核题阻塞标准的 `verification` 记账，用宿主原生问答或文本取得真实的验证范围确认，明确记为手工记录而非 MCP `CONFIRMED`；未确认仍不开工。内建流程不等同于放行任何破坏性操作。

**纯 Skill 回退**：MCP 不可用时用宿主原生问答或文本逐题确认适用项；保持相同模式边界（标准/快速仍用内建执行，标准仍确认验证，只调查仍只读，只有长任务自动用专用角色）。长任务在 Markdown 手工记录选择、工作包、attempt、静止窗口和原始证据；复核路由不变。需要的角色、实际完成证据或完整复核基线不可用时标 `BLOCKED`/`NOT_RUN`，不伪造通过。即使已切换长任务，不能登记并取得当前 `APPROVED` 的破坏性操作仍不执行。恢复 MCP 前显式核对 SQLite 与手工记录，不自动覆盖或迁移。

docs-root 优先 00_doc / 00_docs / docs / doc，均无则 docs。项目与协作记忆、证据在其 agent/，审查报告在 04_test/report/。MCP 初始化缺失记录，主对话维护审查记录；PyQt 可打开已登记库，详见 [Obsidian 视图](references/obsidian.md)。
