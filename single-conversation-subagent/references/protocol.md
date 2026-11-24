# 单对话 subagent：两级协作协议

（主对话（协调层） → 执行子 Agent）

记录日期：2026-10-19（Asia/Shanghai）

## 交互与 MCP 边界

Skill 管策略；本地 Python MCP 管确定性状态、交互与证据；ZCode `Agent` 运行执行和复核角色，主对话验收。stdio 启用 `SCS_NATIVE_REVIEW=1`；MCP 不创建 Agent，不作为执行或复核桥接器。

宿主原生提问 > 声明支持的 MCP 表单 > 文本回退。只使用一路 UI；失败后再明确切换。工具不可见不是默认模式；TEXT_FALLBACK_REQUIRED 必须完整显示选项并结束当轮等待。无法可靠观察 UI 时记 UI_NOT_OBSERVABLE，不宣称已弹窗。

会话绑定真实宿主 conversation ID + 客户端命名空间 + canonical 项目根。无 ID 时新建临时 session_id 并保留在当前对话；补登记或重连时，用原项目绝对路径、原 client_name 和 resume_session_id 恢复；不从 cwd/PID/连接 ID 推断对话。新对话重新确认，handoff 只续工作包。

问题按 NOT_ASKED → REQUESTED → WAITING_USER → CONFIRMED；另有 DECLINED/CANCELLED/TIMED_OUT/UNSUPPORTED/FAILED。每题唯一 question_id；重复同值提交幂等，终态不同值冲突；显式 reopen 后新 ID，旧回复隔离。UNSUPPORTED/FAILED 可通过文本提交明确选择；取消/拒绝后先等用户要求重开。真实原生失败可登记 unsupported/error 后重开，用下一路交互；不得并行弹框。

SKILL_NOT_LOADED 仅靠安装/宿主证据判断；TOOL_NOT_CALLED 仅靠宿主日志/回归探针判断。服务只观察声明能力、RPC 和实际回执；不能观察未发生调用或推断 UI 展示。取消、拒绝、错误、超时均不等于授权；不记录提示词全文、密钥、Cookie、私有 URL 或原始推理。MCP API 见 [mcp.md](mcp.md)。

## 结构

主对话是第一级协调者，负责目标、决策、记忆、调度、证据和验收；第二级是执行子 Agent（分析/实现/测试）、可选的只读 `方案顾问` 或独立只读审阅员，均不再委派。不创建“协调子 Agent”。

设计初衷：隔离长执行线，让主对话只保留目标、决策、未决问题、下一步和证据路径；执行细节留在子 Agent 和证据文件。它不是缓存清理机制，主对话仍会随轮次增长。

流程：用户 → 主对话（协调/记忆/调度/汇总/验收）→ 执行子 Agent（分析/实现/测试）→ 主对话。

## 开场四选一

每个对话只在首个任务问一次，包括“推送吧/改一下/跑一下”这类直接命令；同一对话后续交互沿用已选模式，不重问；只有用户明确要求改变执行方式才显式 reopen 对应模式题并记录新选择。只有用户明确说“不用问，直接做”才可跳过。不得因为任务看起来是纯问答、小改、单文件、简单查询或时间紧而自动跳过；任务大小只影响首次推荐项，不决定是否提问；推荐项由主对话在必要只读调查后按范围/风险/改动面/验证成本选择，不固定同一项。

1. **快速处理**：走 ZCode 内建流程，主对话直接处理，按需用默认/内建 Agent；不问验证范围。
2. **标准执行**：先确认验证范围，再按 ZCode 内建流程计划、实现和验证，按需用默认/内建 Agent；不依赖 SCS 工作包。
3. **长任务**：分阶段，计划前问验证范围；只有此模式自动编排六个专用角色。任务性查找、分析、实现、测试均按角色委派，主对话只保留目标、决策、未决问题、下一步、工作包修订、证据路径和协调记录。
4. **只调查**：保持只读，按需用内建 `Explore`，不自动用专用角色、不问验证范围。

只给一个按范围/风险/成本动态决定的推荐项。优先真实调用宿主原生提问；其次 MCP 结构化交互；两者不可用则完整文本展示并结束当轮，等待明确回复。推荐勾选不等于确认，取消和拒绝均不采用默认项。明确“不用问，直接做”可跳过模式选择进入快速；不改变破坏性操作闸门。

仅长任务在验证范围后问 `codex_gate`（`none|closure|packages`）。启用复核后用 ZCode 原生问答确认 `review_route`（`gpt|inherit`）再派发；缺 gate 或所需路由的长任务按 `INTAKE_REQUIRED` 补适用题，不从旧答案推断。其他模式不问 SCS 复核时机、路由或方案指导，不因旧 `codex_review` 或其他历史值自动派审。显式 reopen 上游题只 supersede 未完成的下游题；已确认下游答案保留，只在适用模式中沿用。旧服务产生不适用的复核阻塞时按 [MCP 兼容说明](mcp.md#问题顺序复核时机与-intake) 处理，不补问、代填或绕过。

推荐不是固定映射：主对话先看范围/风险/成本，再选一项并说明理由；例如简单只读/小改常快速处理，需改代码或验证常标准执行，跨阶段/长测试常长任务，纯只读定位常只调查；拿不准可推荐标准执行。

### 长任务的委派边界

长任务不是“主对话多干活”，而是把执行线整体下沉：

- 必须派执行子 Agent：代码/文件检索、依赖与调用链分析、日志分析、方案调研、实现/重构、构建/测试/运行验证、大范围文档阅读。
- 三执行角色按 `kind` → ZCode `Agent.subagent_type` 派发：`analyze` → `查询侦察员`（固定 DeepSeek，只读），`implement` → `实施工程师`（继承模型，只改 `allowed_files`），`test` → `测试验证员`（固定 DeepSeek，只跑指定命令）。职责、提示和返回 schema 见 [roles.md](roles.md)。
- 只有主对话派独立只读复核角色 `固定审阅员` / `继承审阅员`，不替代执行角色；`方案顾问` 只用于单独、可选的只读建议。所有子 Agent 不再委派、不调用外部复核技能或工具。
- 主对话与用户交互，维护 session/工作包/证据/记录，准备有界 diff/哈希和证据索引，持久化原始结果并核对关键证据。
- 主对话发现自己要持续检索、读大文件或动手实现时，先建工作包再派子 Agent，不直接做。
- 每次派发范围有界；子 Agent 回传状态、实际动作、证据路径、首异常、剩余问题、资源释放，不贴原始日志。
- 子 Agent 不提问、不做验收、不写顶层规划和记忆；需要用户决策时回主对话。

## 多子 Agent 并发（长任务）

长任务默认主动识别可并发的独立工作；主对话编排，MCP 只校验/记录，不调度 Agent。支持同一 WP 内多个就绪子任务并发，以及同一 canonical 项目内多个 WP 并发；首版不支持跨项目联合调度。

### 执行单元

- WP 是目标/验收边界；revision 是冻结定义；subtask 是 DAG 节点；attempt 是一次真实执行。
- 子任务可声明 `depends_on`（同包子任务 ID）、`resources`（`file:` / `device:` / `serial:` / `programmer:` 加 `read` / `write`；3.0 文件只支持 `write`，文件 `read` 被拒绝）和 `required`。
- 依赖必须已核验 `PASS` / `SUBSTITUTED`，且没有活动 attempt；证据目录为 `.../R<rev>/<subtask>/<attempt>/`。
- 子 Agent 必须拿到 `attempt_id`；不得直接更新 WP、顶层记忆或验收；主对话串行登记结果。

### 调度

- 先 `work_attempt_prepare` 原子占用资源并建立 attempt；成功后才启动宿主 Agent，禁止先派发后登记。
- prepare 返回 `attempt_id`、`evidence_dir` 和 leases；主对话在证据目录保存结果。子 Agent 不主动写 `allowed_files` 外的证据；复核角色不落盘。
- 生命周期：`PREPARED → LAUNCHING → RUNNING → DRAINING → ENDED`，另有 `UNKNOWN`；`UNKNOWN` 不释放执行槽/资源，也不自动重派。
- 宿主受理派发后保留用于查询的原任务 ID，并与 SCS `attempt_id` 对应。优先由宿主的最终通知驱动后续步骤；等待期间可推进不依赖该结果的工作。只剩依赖项时继续等待，保留一次等待状态，有实质变化再更新用户。
- **通知保底**：经过合理间隔仍未收到通知、会话恢复，或怀疑通知漏达时，对原任务做一次非阻塞状态查询（`TaskOutput(task_id=..., block=false)`，或宿主等价接口）。`running` / `not_ready` 继续等待并拉开检查间隔。等待或查询超时表示本次没拿到结果，不登记任务失败，也不连续调用阻塞等待刷出超时。宿主明确报告成功、失败或停止时，核对实际终态和证据后登记。任务丢失或状态无法可靠确认时用 `UNKNOWN`，维持隔离，等待核实原任务；不能根据等待超时、静默或租约到期重派。
- 结束用 `work_attempt_update(state="ENDED", result=..., operation_id=...)`；主对话串行提交。重复通知只补未登记结果，必要时复用同一 `operation_id`。`VERSION_CONFLICT` 后重读版本，只补未登记结果。
- 默认并发上限 4；没有宿主真实并发证据时取 1。优先级：用户明确事项 > 关键路径 > 解除阻塞的短任务 > 等待时间。
- 依赖未满足或资源未获得时不启动，换其他就绪任务；失败节点停止其后继，独立分支继续。

### 子 Agent 会话续用

只自动续用长任务的 `analyze` / `implement` / `test`。一个 Agent ID 是一条可多轮使用的执行线，不是一个 attempt。主对话在对应 attempt 的证据与协作记录中保存原 ID、同一主会话/项目根、角色和有效 provider/model、工作线及读写/命令授权、最近完成轮次、证据路径，不存全量提示。**同角色、同工作线、基线连续且权限兼容**才复用；预期内的本线改动可继续，发现外部基线变化、角色/模型/工具变更或授权范围变化则不续用旧会话。短模式不自动启用。独立审阅（含包级、收尾、预审）每次新开，方案顾问仍至多一次。

1. 仅从已收到真实终态、已登记 `ENDED`、无进程/设备遗留的执行线中挑选；`running` / `UNKNOWN` 继续等，不发送下一轮、不抢租约。若任务已做完且证据仍有效，直接复用结果，不额外发一轮。
2. 核实占用后决定。优先用宿主可验证的上下文数据；Linux ZCode 4 可运行 `python3 -I <Skill目录>/scripts/zcode-context.py --parent <ZCode主会话ID> --agent <原Agent ID> --model <实际provider/model> [--window <已核验有效窗口>] [--reserve <下轮预算>] [--db <SQLite路径>]`。数据库路径默认以只读模式（`?mode=ro`）读取 `~/.zcode/cli/db/db.sqlite`，支持通过 `--db PATH` 或环境变量 `SCS_ZCODE_DB` 覆盖（优先级 `--db` > `SCS_ZCODE_DB` > 默认路径）。实操核实边界：`--parent` 仅可使用宿主当前对话真实可观测的父会话 `sess_...` ID，不得从目录、数据库最新行或其他会话猜测；`--window` 仅可使用该精确 `provider/model` 在当前 ZCode 配置或运行时明确确认的有效上下文窗口（有更低的网关/部署上限则取更低值），不得借用同名模型、其他 provider 的配置或猜数——**无法确认有效窗口时省略 `--window`**，让脚本安全返回 `UNKNOWN / CONTEXT_WINDOW_UNVERIFIED` 并回退为换新会话。探针只读验证该子会话归属，输出单条 JSON；只用最近一条 `subagent` 请求的输入和输出，缓存命中仍计入占用，不累加每次请求，不用通知的累计 token；除检查 `session.time_compacting` 外，还会检测该子会话在 `model_usage` 中的 `query_source='compact'` 历史压缩行（返回 `UNKNOWN / COMPACTION_STATE_PRESENT`）及 `cache_creation_input_tokens > 0` 或异常 token 字段等口径不确定项（返回 `UNKNOWN / USAGE_NOT_USABLE`），一律安全返回 `UNKNOWN`。默认预留 4096 token，预计下一轮工具输入/结果较大时提高预留。预计超过 70% 则先有界交接再换新；恰好 70% 仍可复用。占用是估算，不保证单轮不会溢出。`UNKNOWN`、不可核实压缩、已确认上下文溢出、或宿主不能安全续用时，明确原因并用摘要开新，不把无法读取当成 0%。
3. 每轮先 `work_attempt_prepare`；续用时传原 `host_agent_id`，成功后用 `SendMessage(to=<原Agent ID>)` 发给已结束的 Agent。给当前 `WP / R / subtask / attempt_id`、本轮读写/命令授权、变化与验收，不重复全量历史，不沿用旧授权。新会话只带已证实结论、工作树状态、证据索引和剩余问题。宿主明确未受理时先结束这轮未执行 attempt，再决定是否换新；不确定是否受理时按 `UNKNOWN` 保留资源，不盲发或改用 `Agent`。
4. 每轮独立核对最新宿主回执，使用本轮工具调用/通知关联和回传的 `attempt_id`；旧通知不得结束新 attempt。仅原 Agent ID 不能区分两轮；补查如果只看见上轮结果也不能结束本轮。登记时单独保存本轮证据，保持 `operation_id` 幂等。恢复主对话时先核对执行线和活动 attempt；无法可靠关联回执时保持 `UNKNOWN`。

**有界交互**：续用消息默认不超过 4 KiB UTF-8；执行回执默认目标不超过 2 KiB UTF-8。始终回显本轮 `attempt_id`，优先报状态、阻塞、高风险发现、首个关键失败、证据引用和资源状态；装不下时用 `unexpanded` 列出未展开的事项 ID、主题和证据定位，可附剩余数量。不要为了压字数把未展开问题写成无问题。

**按需展开**：主对话优先定点读取已有证据；仍缺影响决策的细节时，点名事项分批取回。仅当多项证据必须放在一起才能判断，才在本轮派单中同时写明事项、理由和 `reply_budget_bytes`（大于 2048、最多 8192）；下一轮恢复默认，不因对话已很长而自动放宽。仍放不下就继续拆分，不贴全量日志、diff 或子会话。需要再次调用 Agent 时仍先 prepare 新 attempt、按续用规则判断；补取既有结果是只读任务，不重跑测试、不改源码、不修改已结束 attempt，也不挤进审阅窗口。原 Agent 不符合本轮角色或授权时不续用；可定点读取证据，或为仍需查证的事项派只读侦察。例：`只展开 F2/F4 的关联和证据行；需一起判断是否同一根因；reply_budget_bytes=4096`。正式复核仍独立，保存原始最终消息和唯一结论，不以补充说明替换或裁改原回执。消息预算不是宿主或 MCP 的硬截断。

### 扇出预算

- 调查阶段有 n 个独立只读子任务时，起步并行 min(4, n) 个 `查询侦察员`（`analyze`）；主对话可按依赖和资源追加，但不超过并发上限。
- 实现阶段按 `allowed_files` 两两不相交取最大并行集合；同一文件或目录覆盖范围视为相交，串行。
- 默认并发上限 4；没有宿主真实并发证据时取 1。依赖未满足、资源未获得、版本冲突或复核窗口不允许时不启动。
- 文件不相交只是必要条件，仍以 `work_attempt_prepare` 的资源原子占用和依赖校验为准。

### 资源冲突

- 文件写租约按段判断：目录覆盖后代；W/W、W/R 冲突。3.0 不提供共享文件读租约：analyze/test/review 默认不取租约，显式 `file:` + `read` 返回 `RESOURCE_READ_UNSUPPORTED`；设备类 read 未禁用。
- 旧包已存的文件 read 资源在 prepare 时过滤并返回 `warnings[]`（`READ_LEASE_SKIPPED`），不阻断，但不要把它当成读保护。
- 一次 attempt 的资源集合必须原子获取或全部失败；不允许持有部分资源等待剩余资源。
- 设备按逻辑 ID；首版不建立跨项目设备域。TTL 到期不等于进程停止；旧 attempt 未 `ENDED` 时资源隔离，不能仅凭 TTL 放行新写者。
- 不抢占运行中的写任务；先停止并确认静止，再交接。

### 静止屏障与幂等

- `REVIEW_READY`、`COMPLETED`、`ABANDONED`、`new_revision`、`handoff_export` 前必须没有活动 attempt，否则返回 `ATTEMPTS_ACTIVE`。
- review attempt 必须单独运行：prepare 时包内不得有其他活动 attempt，review 活动期间不得 prepare 其他子任务；批量中 review 必须单独一批。违反窗口约束返回 `REVIEW_WINDOW_ACTIVE`。
- 放弃/异常包先结束或确认停止相关 attempt；有活动 attempt 时禁止用 `work_package_update` 直接写子任务结果，必须走 `work_attempt_update`。
- `work_package_update` / `work_attempt_update` 支持 `operation_id`：同 id 同载荷返回原回执，不同载荷返回 `OPERATION_CONFLICT`。
- 事件记录 attempt 准备、状态变化、结束、资源冲突；不记录提示词全文。
- 首版并发范围：同项目包内/跨包。跨项目设备域、活动 handoff 转移、worktree 自动集成留到后续版本。

## 批量、备注与运行地图

### 批量 prepare

`work_attempt_prepare_many(session_id, package_id, expected_version, subtask_ids, host_agent_ids?=None, ttl_seconds=120)`

- 单包、单事务、全有或全无；入口只做一次 `expected_version` 校验，成功后工作包版本不变。
- `subtask_ids` 为 1..100 个简单 ID 且不重复；缺失子任务返回 `SUBTASK_INVALID`。
- 同批内有依赖关系的子任务不会同时就绪，按波次准备；依赖方返回 `DEPENDENCY_NOT_READY`。
- 含 review 且多于一项返回 `REVIEW_WINDOW_ACTIVE`；review 必须单独准备。
- `host_agent_ids` 省略时全部为 `None`；提供时必须与 `subtask_ids` 一一对应。
- 返回 `attempts[]`（attempt、leases、evidence_dir、warnings）；review 项另带 `review_subject` 和 `review_required`。

### 批量 update

`work_attempt_update_many(session_id, updates=[{attempt_id,result}], operation_id, expected_version?=None)`

- 只接受 ENDED 结果，不传生命周期 state；同包且属于当前修订，跨包/跨修订分别返回 `BATCH_PACKAGE_MISMATCH` / `BATCH_REVISION_MISMATCH`。
- 重复 `attempt_id` 或格式错误返回 `INPUT_INVALID`；全有或全无，任一失败整批回滚。
- 一次成功只把工作包 `version` 加一；`operation_id` 必填，重放先于状态/版本检查并返回原回执，不同载荷返回 `OPERATION_CONFLICT`。可选 `expected_version`（非 bool 整数）启用乐观锁，在重放与 `PACKAGE_CLOSED` 检查后校验版本，不匹配返回 `VERSION_CONFLICT`。
- review 必须单独一项；不能与其他结果混批，否则 `REVIEW_WINDOW_ACTIVE`。
- 批量不改变工作包状态，不替代 `work_package_update` 的状态转换和静止屏障。

### note 与 run_map

`work_attempt_note(session_id, attempt_id, note, note_id)`

- `note` 1..2000 字，`note_id` 为简单标识且必填；同 note_id 同载荷幂等，不同载荷返回 `OPERATION_CONFLICT`。
- 追加一条 events（含 attempt_id/note/note_id/at），不写工作包版本、不续租、不更新 `last_observed_at`；**不证明进程存活**。
- 不替代 attempt 状态更新；结束仍用 `work_attempt_update`。

`run_map(session_id, package_id?=None, revision?=None, limit=100)`

- 只读单事务，返回 `packages`/`subtasks`/`attempts`/`notes`/`blockers`/`review`、`mermaid`、`captured_at`、`truncated`、`limits`；不写文件、不刷新视图。
- 上限：包 ≤64（`limit` 可更小）、attempt ≤200、subtask 展示 ≤500、每 attempt 最多展示 3 条 note；包/attempt/subtask 超限时标 `truncated`。
- `revision` 必须配合 `package_id`，读取当前修订或已存修订快照；不重算 review subject。复审新鲜度仍由 `REVIEW_READY`/`COMPLETED`/`handoff_export` 强制。

## 验证范围（标准执行 / 长任务）

同一对话只在首次标准/长任务问一次，后续沿用；只给一个推荐路径：主对话先做必要的只读调查，再按改动风险、影响面、验证成本、资源和回滚难度选择一个组合（例如小改→本轮冒烟；核心/硬件相关→定向或全量；并发/时序→稳定性）；不固定同一路径。其他选项不标推荐/默认。

| 选项 | 时机 | 耗时 | 资源 | 说明 |
| --- | --- | --- | --- | --- |
| 快速冒烟 | 本轮 | 2 min | 无（只读） | 可作本轮快速反馈 |
| 定向回归 | 收尾 | 15 min | 板卡 A | 按改动面选择 |
| 全量回归 | 收尾 | 2 h | 板卡 A | 备选 |
| 长时间稳定性 | 收尾 | 8 h | 独占板卡/串口 | 备选 |
| 烧录/擦除 | 按需 | 10 min | 烧录器 | 单独确认 |
| 不做测试 | 本轮/收尾 | 0 | 无 | 与测试及其他不测试选项互斥 |
| 不做测试但记录方法 | 本轮/收尾 | 0 | 无 | 与测试及其他不测试选项互斥 |

- 定向/全量回归和稳定性收尾执行，不每轮重复。
- “不做测试”标记 `NOT_RUN`；“记录方法”只写命令/环境/预期结果到 `AGENT_COORDINATION.md`，不标 `PASS`。
- 每次问只给一个推荐路径；未选中的长耗时操作不执行，新增操作走新修订；烧录/擦除单独确认。
- 未回复不采用推荐项。仅认可宿主真实可观测的超时结果与事先告知的配置策略；本版服务只记录 TIMED_OUT，不自动确认。文本回退结束当轮等待下一轮输入；取消/拒绝/工具失败不授权继续。

## 原生方案指导、复核时机与收尾审查

### 时机和路由

| 模式 | 兼容时机字段 | 行为 |
| --- | --- | --- |
| 长任务 | `codex_gate=none` | 无包级或收尾审查 |
| 长任务 | `codex_gate=closure` | 仅收尾一次原生只读审查 |
| 长任务 | `codex_gate=packages` | 每包复核，并在收尾再审；Reviewer 可用时推荐 |
| 标准/快速/只调查 | 无 | 不询问 SCS 复核时机或路由，不自动派方案顾问或审阅员 |

- `packages` 新包冻结 `review_required=true`，新修订沿用；旧包缺失按 `false`。未声明 `review` 子任务时 `REVIEW_READY` 返回 `REVIEW_REQUIRED`，须用新修订补声明。
- 新流程不问 `codex_review`；该字段仅保留旧服务/会话兼容，不是短模式确认项。
- 长任务启用复核时，在派发前通过 `interaction_request(kind="review_route", channel="native")` 确认协议值 `gpt`（正式复核派 `固定审阅员`，固定 GPT）或 `inherit`（正式复核派 `继承审阅员`，`model: inherit`）；开场确认仍须在开始实现、执行测试或派发前完成。沿用已确认路由；不可用不换路由，按相应步骤阻塞。只有用户明确变更才 reopen。长任务操作预审尚无路由时用同一个 `review_route` 题单独确认，不改变时机字段；方案指导独立于该路由。
- 推荐不是确认，未答、取消、失败或超时不得自动选项。迟到回复绑定原问题、工作包与修订；先核对，只调整未执行部分。收尾已过但未实际审查时标 `NOT_RUN`，不得补记通过。

### 长任务方案指导（可选，仅一次）

长任务开工前用原生问答推荐一次只读方案指导（无工具则文本）。用户同意后，主对话单独调用 `Agent(subagent_type="方案顾问")`（固定 GPT）；brief 包括目标、计划改动、风险、约束、验证和回滚。顾问只给建议、不出正式判定；主对话记录原始结果并纳入方案。不可用或拒绝时说明并跳过指导，不改派正式审阅员，不解除正式审查或操作闸门。同一任务不重复；不因短模式出现高风险操作而自动询问或派发。同意不写入时机字段，指导不依赖或改变 `review_route`，也不替代包级、收尾或操作预审。

### 正式收尾

实现和所选验证结束后，主对话按时机字段和确认路由派一次独立 Reviewer；提供有界 diff/哈希、验收标准和原始证据索引，保存实际原始最终消息与宿主完成证据。只认唯一结论行 `结论: ACCEPT` 或 `结论: REJECT`；未实际完成、缺证据、无明确判定或 REJECT 不得报告通过。收尾是 Skill 规则，MCP 只强制 `packages` 的包级复核，不能把包级 ACCEPT 或方案指导冒充收尾。

## 工作包复核（review 子任务）

仅 `codex_gate=packages` 的长任务包必须做。`review` 是子任务 `kind`，记录已确认路由的原生独立复核。

### 顺序与窗口

1. 非 review 子任务按计划完成（`PASS`/`SUBSTITUTED`），包内没有活动 attempt。MCP 强制复核窗口和 `depends_on`；主对话保证其余顺序，后续结果变化会使旧 ACCEPT 变成 `REVIEW_STALE`。
2. `work_attempt_prepare` 单独准备 review attempt；成功返回 `review_subject`，`input_snapshot` 锁定当前 `review_route`。窗口内不得 prepare 其他子任务（`REVIEW_WINDOW_ACTIVE`）。
3. 主对话准备有界 diff、哈希、允许文件、工作包/修订、当前 subject、验收和非 review 证据索引，再按锁定路由派 Agent；按实际宿主回执登记 `host_agent_id`，把原始最终消息与 v2 manifest 写入该 attempt 的 `evidence_dir`。
4. 用单个 `work_attempt_update(state="ENDED", host_agent_id=<实际 ID>, result=...)` 结束：ACCEPT 用 `PASS` + manifest；REJECT 用 `FAIL` + manifest；manifest `agent_id` 与实际 `host_agent_id` 一致。
5. `REVIEW_READY`/`COMPLETED` 要求当前 subject 的 `PASS` + ACCEPT；无 → `REVIEW_REQUIRED`，旧 → `REVIEW_STALE`。`COMPLETED` 和 `handoff_export` 还会重算 subject。

- 不得直接 `work_package_update(results=...)` 写 review（`REVIEW_ATTEMPT_REQUIRED`）；文件、定义或非 review 结果变化后用新 review attempt 重审。
- 角色不可用、超时、缺实际 Agent ID、原始输出残缺/无唯一判定时不得 PASS；停止后按实际状态记非通过，包保持未通过。未确认已经停止时不得假定 attempt 静止。
- `force_close` 是操作员例外，不表示 review-accepted。

### 证据 manifest

严格 v2 JSON 对象：恰好 `schema_version`（整数 2）、`review_subject`、`agent_type`（锁定路由对应的派发 ID：`固定审阅员`/`继承审阅员`）、`agent_id`（实际宿主完成 ID）、`model`（可观测 `provider/model` 或 `unobserved`）、`status`（`completed`）、`verdict`（`ACCEPT`/`REJECT`）、`final_message`（`{path,sha256}`）。路径相对 manifest，须留在本 attempt 证据目录；原始 UTF-8 最终消息的哈希匹配且恰好一行结论，与 `verdict` 一致。不加 shell 退出码或命令；不接受正文子串代替结论。具体格式见 [MCP 用法](mcp.md#review-子任务与证据)。

### review_subject

覆盖：

- 修订定义：`goal`、`allowed_files`、`acceptance`、非 review 子任务定义（按 id 排序）；
- 非 review 子任务的 `status` 和 `evidence(path,sha256)`；
- 包 `allowed_files` 的有界快照：regular 文件 path → sha256，符号链接 path → `readlink` 目标的原始字节的十六进制（有链接时才出现 `links` 字段）；文件与链接合计计入条目上限，总字节数只用于上限。
- payload 顶层含显式 `subject_version`（当前 `2`），参与摘要。旧 payload 不含该字段，升级后重算必然不同，因此升级前记录的 ACCEPT 一律表现为 `REVIEW_STALE`，必须重新 review（安全方向：宁可重审，不误放行）。

链接契约：

- scope 路径的每一级部件（祖先和末级）都不得是符号链接；任一级为符号链接返回 `REVIEW_SUBJECT_INCOMPLETE`，防止读取项目根之外的内容。
- 进入 scope 后遇到符号链接不跟随、不读取目标，只记录相对路径和 `readlink` 目标；目标按原始字节无损编码（十六进制）进入 subject，不经过任何带替换的文本解码，因此非 UTF-8 目标与字面 ASCII 拼写不会碰撞；任何字节级改向（新增、删除或改向链接，含非 UTF-8 目标）都会改变 subject，旧 ACCEPT 不能继续放行。
- 路径名称（文件名、链接名、目录名）在 canonical JSON 中按 JSON 转义无损编码：非 UTF-8 名称经 surrogateescape 得到的字符以 `\uXXXX` 转义写出，canonical JSON 为纯 ASCII，编码不再抛 `UnicodeEncodeError`；转义可逆且单射，实际字符与字面 `\uXXXX` 文本不会混淆，也不使用 `surrogatepass` 产出非法 UTF-8。
- 排除树（`.git`/`.venv`/`.venv-mcp`/`.scs`/`.archify` 和 `<docs_root>/agent/evidence`、`<docs_root>/agent/acceptance`、`<docs_root>/agent/view-archives`）内的链接直接跳过、不记录。
- fifo/socket/设备等非 regular/dir/symlink 的特殊文件返回 `REVIEW_SUBJECT_INCOMPLETE`。
- `allowed_files` 中的 `./` 和重复分隔符按路径部件归一化后遍历、去重；绝对路径、`..` 和反斜杠仍被拒绝。

排除 `.git`/`.venv`/`.venv-mcp`/`.scs`/`.archify` 和 `<docs_root>/agent/evidence`、`<docs_root>/agent/acceptance`、`<docs_root>/agent/view-archives`；上限 500 条目/100 MB（链接也计入条目上限），超限 `REVIEW_SUBJECT_TOO_LARGE`；读取失败/不可读 `REVIEW_SUBJECT_INCOMPLETE`。摘要使用稳定排序，证据目录与受管视图输出变化不影响结果；其他在 `allowed_files` 内的文件或链接变化会改变 subject，并在下一次重算表现为 `REVIEW_STALE`。

## 破坏性操作闸门

闸门适用于所有模式，与开场的包级/收尾复核时机无关。只调查保持只读。标准/快速一旦遇到须走闸门的操作，阻塞该操作并说明原生预审须由用户明确切换长任务后才能启动；主对话不自动升模式，不发起短模式复核题，不用内建/手动角色、旧选择或已有批准绕过。只有用户明确要求切换后才按长任务完成适用确认及以下流程。

### 触发分类

必须走闸门：

- 删除、覆盖、移动用户数据或系统关键配置；
- 擦除、烧录、刷写设备或固件；
- `drop database` / `drop table` 等破坏性数据库操作；
- 不可逆 git 操作（`reset --hard`、`clean -fdx`、强制推送等）；
- 递归 `chmod` / `chown`；
- 卸载关键系统/运行时包；
- 在 `allowed_files` 之外的大范围改写。

不走闸门：普通代码编辑、证据写入（`<docs-root>/agent/evidence/`）、`project_view_refresh` 视图刷新、只读调查。

### 流程

1. 主对话准备精确命令行（或 argv 拼接）、项目根内的绝对 `cwd`、目标（项目相对路径或设备逻辑 ID）、影响、备份、回滚和可选 `baseline`。用新 `operation_id` 与完整参数调用只读 `operation_gate_check`；`MISSING` 返回候选 `operation_digest`，不是放行。
2. 主对话按已确认路由派原生 Reviewer 只读预审；无路由先单独确认（即使 `codex_gate=none`），再提供当前 digest、实际命令、范围、影响、备份、回滚和有界证据。主对话保存真实 Agent 的原始最终消息，以项目内路径及 sha256 固化。
3. 调用 `operation_gate_record`：
   - `ACCEPT`：原生 v2 `review_receipt` 必须绑定当前 `operation_digest`，唯一结论须为 ACCEPT，且有用户独立确认 `user_confirmed=true`；否则不批准。
   - `REJECT`：以同样的原生回执记录 REJECT；同 `operation_id`/digest 是终态，后续 ACCEPT 返回 `GATE_REJECTED`。
   - `UNAVAILABLE_OVERRIDE`：仅原生复核不可用时据实记录，需要明确用户确认且原因不少于 10 字，可附失败/残缺报告；该记录不是 APPROVED。
4. 紧接执行前，以实际命令、`cwd`、目标和基线再次 `operation_gate_check`；仅 `APPROVED` 且仍有用户独立确认才执行。任一绑定参数变化得 `STALE`，必须重新预审与登记。回执格式见 [MCP 用法](mcp.md#破坏性操作闸门)。

### 绑定与幂等

- `operation_digest = sha256(canonical JSON{command, cwd, targets 排序, baseline})`。目标按排序后的集合比较，顺序不影响结果；`baseline` 省略时按 `null` 参与 digest。
- 记录按 `session_id` + `operation_id` 归属；换会话后旧记录不可见，需在原会话登记或重新登记。
- 同 `operation_id` + 同 digest + 同 verdict 返回原记录；同 `operation_id` 不同 digest 返回 `OPERATION_CONFLICT`。已有 REJECT 后不得写 ACCEPT。
- 事件只存有界摘要（digest、command、cwd、targets、impact、backup、rollback、verdict、report_path、report_sha256、user_confirmed、reason、baseline、at），总长不超过 16 KB，不存报告全文。

### 不可绕过

- `REJECT` 不可绕过；需要改方案时使用新的 `operation_id`，不得覆盖原记录。
- 原生复核超时、未完成、缺实际完成 ID、输出残缺、报告缺失或结论行不唯一，都不得放行，也不得记为 ACCEPT。
- 复核不可用时只能记 `UNAVAILABLE_OVERRIDE`：必须用户明确二次确认并写明原因；该状态永远不等同于 `APPROVED`。
- `operation_gate_check` 的 `APPROVED` 只表示“存在当前命令/目标/基线对应的 ACCEPT 记录且用户已确认”，不是执行授权本身。

### 诚实边界

MCP 不执行宿主命令，也没有执行前 Hook，无法拦截真实命令。没有宿主 Hook 时，本节是协议级约束（`PROTOCOL_ONLY`），不是执行级强制；不得宣称已实现内核级拦截或命令执行拦截。宿主若提供 Hook，应在执行前调用 `operation_gate_check` 并以 `APPROVED` 作为放行条件，同时保留用户确认。

## 工作流程

先确认四选一。标准/长任务确认验证范围；标准/快速随后走 ZCode 内建流程，只调查保持只读。以下工作包编排仅为长任务的自动流程：

1. 确认 `codex_gate` 和适用的 `review_route`；按条件推荐一次只读方案指导。
2. 主对话定义工作包：目标、验收、目录、基线、允许/禁止操作、子 Agent 类型、证据要求；`packages` 包声明 `review` 子任务。
3. 主对话同步 SQLite 与 Markdown，服务只初始化缺失文件。断开 MCP 不改变模式边界；用文本/原生问答确认适用项，长任务仍用原生专用 Agent，并手工维护同样的工作包、attempt、静止窗口和证据；恢复时先核对，不盲目导入。缺真实角色或复核证据时标 `BLOCKED`/`NOT_RUN`；无法获得当前闸门 `APPROVED` 时不执行破坏性操作。
4. 按扇出预算拆分和 prepare，再用实际 `subagent_type` 派执行子 Agent；提示含任务、范围、禁止事项、检查和返回格式。review attempt 单独运行。
5. 子 Agent 按 [roles.md](roles.md) 回传状态、动作、证据来源、首异常、剩余问题和资源释放；主对话核对并持久化证据、串行登记。`packages` 包进 `REVIEW_READY` 前须当前 subject 的 ACCEPT。
6. 按确认时机派收尾审查；主对话独立验收 diff/日志/测试/产物并更新结论。压缩/重启时从 `AGENT_COORDINATION.md` 恢复。

## 文档输出位置

记忆根 = docs-root/agent：docs-root 取首个存在的 `00_doc` / `00_docs` / `docs` / `doc`；均无则新建 `docs`。`AGENT_PROJECT.md`、`AGENT_COORDINATION.md`、`evidence/` 都在 `docs-root/agent/`。

WP 审查报告固定 `04_test/report/`。其余正式文档落 docs-root（不是 `agent/`）；不因目录规则自动迁移既有文件。

| 顺序 | docs-root | 条件 |
| --- | --- | --- |
| 1 | `<项目根>/00_doc/` | 存在就用 |
| 2 | `<项目根>/00_docs/` | 存在就用 |
| 3 | `<项目根>/docs/` | 存在就用 |
| 4 | `<项目根>/doc/` | 存在就用 |
| 5 | `<项目根>/docs/` | 都不存在时新建 |

## 记忆与上下文预算

docs-root = 首个存在的 `00_doc` / `00_docs` / `docs` / `doc`；均无则新建 `docs`。默认路径：

| 文件/目录 | 维护者 | 内容 |
| --- | --- | --- |
| `<docs-root>/agent/AGENT_PROJECT.md` | 主对话 | 目标、决定、撤回方案、进度、验收 |
| `<docs-root>/agent/AGENT_COORDINATION.md` | 主对话 | 阶段记忆、子任务、依赖、证据索引、异常 |
| `<docs-root>/agent/evidence/WP-<id>/R<rev>/<subtask-id>/` | 主对话 | 完整日志、测试输出、截图、原始复核消息与 manifest |

- 三级记忆：主对话摘要 → 协作记录 → 证据文件；主对话只读关键证据。
- 子 Agent 默认短回执；细节按[有界交互与按需展开](#子-agent-会话续用)补取，不贴原始日志。
- 每个工作包/阶段结束写 handoff；主对话接近 60%–70% 上下文或压缩时，新开会话，从文件恢复。
- 长测试后台化：子 Agent 返回 PID/日志/状态/停止方式后结束，主对话按检查点查询。
- 子 Agent 在轮次边界按[会话续用规则](#子-agent-会话续用)选择续用或换新，不因容量中途打断运行轮。
- 同一对话内保持系统提示/AGENTS/skill 稳定；规则变更新会话生效。
- 项目适配：只读视图（如 Obsidian acceptance vault）不是事实源；源仍是 `AGENT_*` 与 evidence，视图只读、可重生成；vault 根=docs-root。
- 与桥接模式共存时先明确冲突处理与文件名；未经用户明确授权，不改默认 `AGENT_PROJECT.md` / `AGENT_COORDINATION.md`，也不改桥接受管文件。

## 回传格式

子 Agent 最终消息至少包含：状态、实际动作、证据路径、首异常、剩余问题、资源释放；字段 schema 和 persona 见 [roles.md](roles.md)。主对话阶段汇总可用：

```json
{
  "work_package": "WP-<id>",
  "revision": 1,
  "state": "REVIEW_READY",
  "memory": "事实、假设、排除项、关键决定",
  "children": [{"id": "agentId 或 T1", "task": "...", "status": "PASS", "evidence": "<docs-root>/agent/evidence/..."}],
  "risks": ["..."],
  "next_steps": ["..."],
  "needs_user_decision": ["..."]
}
```

`state`：`REVIEW_READY` / `PARTIAL` / `BLOCKED`。子任务状态：`PASS` / `FAIL` / `BLOCKED` / `NOT_RUN` / `SUBSTITUTED`；没有证据不能标 `PASS`。

工作包终态：主对话验收通过后用 `COMPLETED` 关闭；重复、被取代或不再需要的用 `ABANDONED` 关闭。终态必须带 `close_reason`，服务写入 `closed_at`、`closed_by_session` 和事件记录，之后不可修改；后续工作新建工作包。主对话用 `work_package_update`；桌面管理器用本地操作员工具 `work_package_close`，从包内读取 owner session，不能借此跳过版本或原因校验。阶段收尾时核对长期 `PENDING/IN_PROGRESS` 的滞留包和重复包，按实际情况关闭，避免记录一直挂在“待开始”。桌面管理器默认隐藏终态，可用“显示已关闭”查看。

## 边界

1. 只有主对话创建子 Agent；子 Agent 不继续委派。
2. 子任务范围 ≤ 父工作包；不授予文件/网络/提交/推送/烧录权限。
3. 调查只读；测试不改源码/不重建/不换镜像；实现只改允许文件。
4. 同文件/同设备串行；不重复提交；停止确认终态后释放资源。
5. 编译成功、任务结束、现场恢复、根因修复分别报告；最终由主对话验收。
6. 子 Agent 不改顶层规划/记忆文件/验收/安全策略；冲突回主对话。
7. 不伪造 ID/状态/证据；取不到 `agentId` 记“ID 不可得”。
8. 无法创建真实子 Agent → `BLOCKED`，不静默降级；只有用户授权才单级回退。
9. 回传有界；长测试后台化；接近上下文上限写 handoff 并新会话。
10. 不记录密钥/Cookie/私有 URL/原始推理；用户明确指令优先。
11. 四选一仅本对话首次任务询问；后续沿用明确确认的选择，不跨新对话复制。
12. 验证范围仅本对话首次标准/长任务确认；未选项不执行，推荐不是授权。
13. 每个对话只在首个任务发起四选一；后续交互不重问；不得按任务大小/类型自动跳过。唯一例外是用户明确说“不用问，直接做”。
14. 只有长任务自动编排六个专用角色并询问 `codex_gate` 和适用的 `review_route`，不静默换路由。标准/快速/只调查不问 SCS 复核题。仅用户明确点名可在自动流程外手动调用角色，不改变模式或代替正式复核/闸门。取消、拒绝、错误、超时保持未确认。
15. 生成的正式文档按 `00_doc/` → `00_docs/` → `docs/` → `doc/` → 新建 `docs/` 的顺序落盘；协议记忆、审查报告、证据保持既有路径，不改道。
16. 四选一和验证范围各只标一个推荐，且由主对话在必要只读调查后按范围/风险/成本选择，不固定同一项；推荐不免问。
17. 用户明确要求改变执行方式/验证范围时重开对应题，不重新弹全套问题。
18. `review` 子任务记录主对话派发的独立原生只读 Reviewer；执行子 Agent 不参与复核或落盘复核证据。
19. 不得伪造 review ACCEPT、gate ACCEPT 或 `UNAVAILABLE_OVERRIDE`；原生复核不可用时如实记录并保持未通过。
20. 3.0 不提供共享文件读租约；非 implement 默认不取文件租约，显式 `file:` read 被拒绝；旧包 read 资源在 prepare 时过滤并警告。

## 与桥接模式的区别

| 维度 | 三层桥接 | 单对话两级 |
| --- | --- | --- |
| 协调者 | 外部桥接会话 | 主对话自己 |
| 记忆 | 桥接自动写 | 主对话显式写 |
| 核验 | 父子目录自动核验 | 主对话核对 diff/日志/测试/产物 |
| 适用 | 跨工具、程序化核验 | 单对话快速拆分 |

## 原生接入

```text
Agent(
  description="<任务>子 Agent",
  subagent_type="<查询侦察员|实施工程师|测试验证员>",
  run_in_background=false,
  prompt="""
工作包 WP-<id> / R<n> / attempt <id>；工作目录 <cwd>；基线 <...>。
任务与验收：<...>；允许的文件、设备和命令：<...>；禁止：<...>。
不再委派、不修改工作包状态、不另写证据。返回状态、动作、已有证据、首异常、剩余问题和资源释放。
"""
)
```

不传 `model` 覆盖。正式复核用同一 `Agent` 工具，将 `subagent_type` 选为该次确认/锁定路由对应的 `固定审阅员`（`gpt`）或 `继承审阅员`（`inherit`），按 [roles.md](roles.md) 构造只读提示，不让执行 Agent 兼任。可选指导经用户同意后单独调用 `Agent(subagent_type="方案顾问")`，只给建议。

## 检查清单

- [ ] 按模式确认四选一和适用验证；仅长任务确认复核时机和适用的 `review_route`，可选指导经同意后至多单独派一次 `方案顾问`，不替代任何正式复核。
- [ ] 快速/只调查跳过验证题；只调查按需用只读内建 `Explore`；标准/快速按内建流程执行、只按需用默认/内建 Agent。长任务使用 ZCode 的 `查询侦察员`、`实施工程师`、`测试验证员` 和扇出预算，无下一级委派或外部复核回退。
- [ ] `packages` 包声明 `review`；review attempt 单独运行，由所选只读角色执行；`REVIEW_READY` 前的 PASS review 带实际 Agent ID、当前 subject 和原始 ACCEPT 的 v2 manifest；`REVIEW_STALE` 已新开 attempt。
- [ ] 批量 prepare/update 单包原子；`work_attempt_update_many` 的 `operation_id` 已提供；`work_attempt_note` 未被当作存活证明；`run_map` 有界且未写文件。
- [ ] 非 implement 未声明文件读租约；旧包遗留 read 资源已被过滤并提示；同文件/同设备仍串行。
- [ ] 未选中的长耗时操作没有进入计划；破坏性操作已按「破坏性操作闸门」登记，并在执行前 `operation_gate_check` 为 `APPROVED`；REJECT/UNAVAILABLE_OVERRIDE 未被当作放行。
- [ ] 证据已落盘，主对话检查了关键证据和实际产物；无证据不标 PASS。
- [ ] 正式文档落在 `00_doc/`（或回退 `00_docs/`/`docs/`/`doc/`）；协议记忆、审查报告、证据未改道。
- [ ] 阶段结束写了 handoff；上下文接近上限时新会话恢复。


## Obsidian 项目管理

用户启用时以 docs-root 为独立库根，agent/acceptance/ 是结构化工作包的可重生成浏览视图；源记忆和证据保持原职责。project_view_refresh 生成页面并可开启 MCP 工作包变更后的自动刷新；project_view_info 只读检查并返回打开链接。批注保留区之外的编辑冲突时停止视图覆盖，已保存的工作包不回滚。无需社区插件，不改全局库注册；首次打开库需宿主/用户完成。详见 [obsidian.md](obsidian.md)。
