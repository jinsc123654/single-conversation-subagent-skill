---
name: single-conversation-subagent
description: 按任务使用 ZCode 原生 Agent 身份；日常任务直接处理，长任务合并开场确认并编排执行与独立复核。
---

主对话负责用户交互、派发、证据和验收；MCP 只校验和记账，子 Agent 只按派单执行。ZCode stdio 使用 `SCS_NATIVE_REVIEW=1`；不调用外部 Codex、复核技能或工具。

**常规流程只用本页和宿主工具 schema。** 待查找的 SCS 工具按阶段一次选齐（开场、建包/派单）；已可调用就不再查找。不搜索 SCS 源码、安装目录或角色文件来确认工具和角色；以宿主注册表和实际工具返回为准。`references/` 只在兼容、恢复、闸门或确有未覆盖问题时定点查阅，不是开场清单。子 Agent 的角色契约已随角色配置加载；不要让子 Agent 再加载此 Skill 或查阅 SCS 协作协议。若任务本身研究 SCS，只读派单明确给出的目标文件。

## 开场与 Agent 身份

1. 优先采用用户已明确指定的模式、角色和范围。清楚的小改动直接快速处理，清楚的只读请求直接调查，其余范围明确的日常改动用标准执行。长任务须用户明确选择。只有模式选择会实质改变范围、授权或协作方式且无法从请求判断时，才问一次四选一。沿用本对话已选模式，显式变更才重开。
2. 日常任务按需要直接用 `Agent(subagent_type=...)`，无需先开 MCP 会话或查工具。只读调查可用 `查询侦察员`；标准执行若能独立派单，可用 `查询侦察员`、`实施工程师` 或 `测试验证员`。快速处理通常由主对话完成。用户点名且角色可用时直接用该身份，任务权限不因角色改变。使用宿主已注册的角色名，不覆盖模型；缺少点名角色则报告。标准执行说明打算做的验证；高成本、占用设备或范围不明确时才单独确认。普通验证不用另弹窗。
3. 长任务或需要持久化协作记录时，直接调用已可见的 `session_open`。项目根用用户指定目录或当前工作目录，保留真实 conversation_id 和原 client_name。初次绑定且模式已经明确、schema 支持时传 `initial_mode`；续接用 `resume_session_id`，核对返回的状态，沿用已保存的选择。`review_registration` 依据宿主已注册的正式审阅员，未知就传 `unknown`；到复核时再核实。需要项目视图且相关写入获准时再登记 Obsidian，常规开场不创建视图。
4. 长任务只用一张卡片确认验证范围和复核策略；用户已给出的选项直接记录，已确认的值直接沿用。选择 `packages`（每包与收尾复核）、`closure`（仅收尾）或 `none`。默认推荐按任务风险说明；推荐、取消或超时均不入库为确认。卡片答复写入成功后可派发执行角色。审阅员的 GPT／继承路线在首次实际复核之前确定，适用于包级、收尾和操作预审；已有明确偏好且角色可用时直接记录或沿用，不重复问。复核要求与路由冲突时，只阻塞相应复核和依赖它的放行，无须挡住独立的执行任务。
5. 用户明确要求只读方案指导时，才调用 `Agent(subagent_type="方案顾问")`。给出目标、计划、风险、约束和回滚。顾问只给建议，不作为正式审阅或操作预审。

## 提问与答复记录

- ZCode 4 的 `session_open`、`interaction_request`、`interaction_record`、`interaction_submit` 已标记常驻；schema 可见就直接调用。旧宿主确实未曝光时，一次搜索当阶段所需工具。模式确需询问且已绑定会话，用 `interaction_request(kind="mode", channel="mcp")`。首次实际复核缺路线，用 `interaction_request(kind="review_route", channel="mcp")`。收到 `CONFIRMED` 后直接使用，不另提交。旧工具没有 `mcp` 参数时用 `native`；配置为 `text` 时用文本。
- 长任务缺验证和复核策略时，一次原生 `AskUserQuestion` 传两个 `questions`。验证题多选，常用项用 `smoke`（冒烟）、`targeted`（定向）、`none`（不测试）、`method-only`（只记录方法）；在题干说明 `full`、`stability` 或自定义组合可通过自填明确选择。`none`、`method-only` 各自排除其他验证项。策略题用三个策略值单选。缺一项时只问该项；宿主无原生卡片或配置为文本时，在同一条消息给出缺项并等待明确回复。
- 原生卡片收到实际答复后，用 `interaction_record(session_id, selections={verification:[...], codex_gate:...}, source="native")` 一次写入；用户已用消息明确给出的项目用 `source="text"`，只提交明确回答。不要为合并卡片逐题调用 `interaction_request`。已有待答题号时用原题的 `interaction_submit`；变更已确认选择须 `interaction_reopen(explicit_user_change=true)`，再记录新答复。旧版没有 `interaction_record` 时，卡片答复按验证、策略的顺序用既有接口保存，不再弹一次。旧版返回 `INTAKE_REQUIRED` 且原因是 `REVIEW_ROUTE_REQUIRED` 时，仅在受影响的协作操作前确认路线。紧凑理由以一句话为限。
- MCP 单选返回 `NATIVE_QUESTION_REQUIRED` 或可回退的 `TEXT_FALLBACK_REQUIRED` 且仍待答时，用原题号、题干和选项改走原生或文本。取消、拒绝、超时、失败和推荐值均不确认。不要用同一道题并行打开两个 UI。

## 长任务执行

任务性查找、分析、实现和测试都派执行子 Agent；主对话仅协调、制备有界复核输入、持久化证据并核对关键证据。角色中文名仅是派发 ID，不限制主会话、派单或子 Agent 的回复语言；自然语言按用户要求和任务需要选择，机器校验的证据字段和结论标记保持原格式。

- **角色**：`analyze` → `查询侦察员`（只读）；`implement` → `实施工程师`（仅编辑 `allowed_files`，不运行命令）；`test` → `测试验证员`（只在授权 `cwd` 跑指定命令）。用 `Agent(subagent_type=...)`，不覆盖模型。没有对应角色就标 `BLOCKED`，不假冒或静默改由主对话执行。
- **扇出**：独立侦察起步 min(4, n)，实施取文件范围不相交的最大并行集合；默认上限 4，无宿主并发证据取 1。依赖分波次，同文件/设备串行。

### 建包、派单、落证据

1. `work_package_create(session_id, goal, allowed_files, acceptance, request_key, subtasks=[{id,kind,allowed_files,acceptance,depends_on?}])`。范围用项目相对路径，不用 glob。保留返回的包 ID、revision、version；`work_package_update(session_id, package_id, expected_version, state="IN_PROGRESS")` 开工。
2. 先 `work_attempt_prepare(session_id, package_id, expected_version, subtask_id)`，再派对应 Agent。并发可用同包原子 `work_attempt_prepare_many`，传 `subtask_ids`；prepare 不改版本。只记录宿主实际返回的任务 ID 与 Agent ID，不猜、不混用。
3. 派单只给本轮数据：`WP/R/subtask/attempt_id | 项目根、实际 cwd、基线 | 目标和验收 | 允许/禁止范围 | 候选文件或证据的具体路径/行号 | 命令及日志路径（若适用）`。已知入口直接给文件；测试只给实际命令和预期，不要求重新找入口。审阅给有界 diff/哈希、当前 subject、证据路径和待核验项；允许在授权范围补查，不能因缺证据而默认接受。不要把宿主默认 cwd 当作授权 cwd。
4. 执行角色回显 `attempt_id`、状态、实际动作与关键 `file:line` / 输出；实施附改动文件，测试附实际命令、cwd、退出码和通过/失败数，遇阻附首个异常及资源状态。主对话把实际回执写入返回的 `evidence_dir`，计算 SHA-256。分析/实施可用 Markdown；测试 `PASS` 需 JSON `{"command":"实际命令","exit_code":0,"passed":1}`（`passed` 填实际正整数），可另存 Markdown 给人读。
5. 核对终态与证据后串行 `work_attempt_update(session_id, attempt_id, expected_version, state="ENDED", host_agent_id, result={status,evidence:[{path,sha256}]}, operation_id)`，用项目相对证据路径。提交会校验证据，无需对同一份证据另跑 `evidence_verify`（需要独立检查时才用）。更新成功取返回的 version；版本冲突先重读。非 review 的并行结果可用 `work_attempt_update_many(session_id, updates=[{attempt_id,result}], operation_id, expected_version)`。原载荷重试复用 `operation_id`。

### 续用、复核与关闭

- **执行会话优先复用**：长任务的查询、实施、验证，在同一工作流与授权范围内优先用 `SendMessage` 续用已结束的原 Agent；每轮先 prepare 一个新 attempt，只发增量和本轮授权。已核验的预计占用超过 70% 才按容量换新；上下文不可核验、宿主不支持续用或身份/权限不匹配时安全回退。正式审阅仍独立新开。执行回执默认简短；有未展开事项时按[续用与有界回传](references/protocol.md#子-agent-会话续用)点名补取，不默认放宽。
- **后台等待**：保留宿主任务 ID 与 attempt 的对应关系，优先接收完成通知。先推进独立工作；依赖结果时保持等待。经过合理间隔仍未收到通知，或会话恢复后，用原任务 ID 做一次非阻塞状态核对。若仍在运行，隔一段时间再查，只在状态有实质变化时更新用户。查询超时仍按等待处理；只有查不到可靠状态才记 `UNKNOWN`，不得据此结束 attempt、释放资源或重派。详见 [调度](references/protocol.md#调度)。
- **包级复核**：`packages` 包声明一个 `review` 子任务。其余子任务完成且无活动 attempt 后，先确认适用的审阅员路线，再单独 prepare 并按 `input_snapshot` 锁定的 `review_route` 派只读 `固定审阅员`（`gpt`）或 `继承审阅员`（`inherit`）。原样保存 Agent 最终消息为 `final-message.md`，以真实 `host_agent_id` 单独 `ENDED`，证据引用 `manifest.json`。原生 v2 manifest 只含 `schema_version:2`、`review_subject`（prepare 值）、`agent_type`（中文派发 ID）、`agent_id`（真实完成 ID）、`model`（宿主可观测值或 `unobserved`）、`status:"completed"`（有真实完成才填）、`verdict`、`final_message:{path:"final-message.md",sha256}`。原文须恰好一行 `结论: ACCEPT` 或 `结论: REJECT`，不补写、不改稿。ACCEPT 记 `PASS`，REJECT 记 `FAIL`；只有当前 subject 的 ACCEPT 放行。复核窗口禁止其他 attempt；文件、定义或非 review 结果变化即 `REVIEW_STALE`，用新 attempt 重审。不能用 `work_package_update(results=...)` 直接写 `review`。
- **静止与终态**：有活动 attempt 时不得 `REVIEW_READY`、`COMPLETED`、`ABANDONED`、切修订或 handoff。核对所选验证、复核及证据后，用 `work_package_update` 先置 `REVIEW_READY` 再置 `COMPLETED`，重复/被取代用 `ABANDONED`；每次传当前 `expected_version`，终态带 `close_reason` 且不可修改。

## 破坏性操作闸门（全模式）

删除/覆盖用户数据或系统关键配置、擦除/烧录、drop 数据库/表、不可逆 git 操作、递归 `chmod`/`chown`、卸载关键包、`allowed_files` 外大范围改写等均需闸门。标准/快速如遇此类操作，立即阻塞该操作并说明原生预审须由用户明确切换到长任务后才能启动；不自动切换，不在短模式补问路由，不用内建/手动角色或历史批准绕过。只调查不执行此类操作。

长任务中，主对话冻结命令、`cwd`、目标、影响、备份、回滚和基线，准备有界输入和 `operation_digest`；由确认路由的原生 Reviewer 只读预审（无路由时单独确认，即使 `codex_gate=none` 也不免除），主对话落证据并 `operation_gate_record`。执行前以实际参数 `operation_gate_check`；只有 `APPROVED` 且保留用户独立确认才执行。ACCEPT 需当前 digest 的原生证据、唯一结论及 `user_confirmed=true`；REJECT 对同 `operation_id`/digest 是终态。复核不可用只能如实记 `UNAVAILABLE_OVERRIDE`（用户确认及理由），不是放行。MCP 不执行或拦截命令；无宿主 Hook 时是协议级约束。普通代码编辑、证据写入、视图刷新不触发。详见 [完整协议](references/protocol.md#破坏性操作闸门)。

## 收尾与纯 Skill 回退

按真实执行结果报告验证和证据，未执行标 `NOT_RUN`。长任务选了 `packages` 或 `closure` 时，在实现和所选验证结束后，用已确认路线独立收尾一次；缺路线此时询问。未实际完成、缺证据或 REJECT 均不能报告复核通过。主对话核对证据后验收。

MCP 无法使用时，日常原生 Agent 仍可直接执行已授权的工作；长任务仍可用原生卡片或文本合并确认，将实际派发 ID、范围、结果和证据写入手工记录。无法取得必要角色或可核验结果时标 `BLOCKED` 或 `NOT_RUN`。恢复 MCP 前显式核对手工记录和 SQLite。破坏性操作仍须取得当前操作的 `APPROVED` 与用户独立确认。

docs-root 优先 00_doc / 00_docs / docs / doc，均无则 docs。项目与协作记忆、证据在其 agent/，审查报告在 04_test/report/。MCP 初始化缺失记录，主对话维护审查记录；PyQt 可打开已登记库，详见 [Obsidian 视图](references/obsidian.md)。
