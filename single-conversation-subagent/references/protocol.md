# 单对话 subagent：两级协作协议

ZCode 的执行身份和固定审阅员以 [角色映射](roles.md#zcode-注册身份) 为准。依赖波次、并行资源、验收以及复核策略与通用流程共用。文中 Codex 审阅步骤在 ZCode 由主对话独立派发 `固定审阅员` 并记录 [原生回执](mcp.md#zcode-固定审阅员回执)；选项由 `codex_gate` 或 `codex_review` 记录。

（主对话（协调层） → 执行子 Agent）

记录日期：2026-10-19（Asia/Shanghai）

## 交互与 MCP 边界

Skill 管策略；本地 Python MCP 管确定性状态、交互与证据；宿主原生 Subagent 执行，主对话验收。MCP 不创建 Agent，不成为独立 Agent 桥接器。

宿主原生提问 > 声明支持的 MCP 表单 > 文本回退。只使用一路 UI；失败后再明确切换。工具不可见不是默认模式；TEXT_FALLBACK_REQUIRED 必须完整显示选项并结束当轮等待。无法可靠观察 UI 时记 UI_NOT_OBSERVABLE，不宣称已弹窗。

会话绑定真实宿主 conversation ID + 客户端命名空间 + canonical 项目根。无 ID 时新建临时 session_id 并保留在当前对话；补登记或重连时，用原项目绝对路径、原 client_name 和 resume_session_id 恢复；不从 cwd/PID/连接 ID 推断对话。新对话重新确认，handoff 只续工作包。

问题按 NOT_ASKED → REQUESTED → WAITING_USER → CONFIRMED；另有 DECLINED/CANCELLED/TIMED_OUT/UNSUPPORTED/FAILED。每题唯一 question_id；重复同值提交幂等，终态不同值冲突；显式 reopen 后新 ID，旧回复隔离。UNSUPPORTED/FAILED 可通过文本提交明确选择；取消/拒绝后先等用户要求重开。真实原生失败可登记 unsupported/error 后重开，用下一路交互；不得并行弹框。

SKILL_NOT_LOADED 仅靠安装/宿主证据判断；TOOL_NOT_CALLED 仅靠宿主日志/回归探针判断。服务只观察声明能力、RPC 和实际回执；不能观察未发生调用或推断 UI 展示。取消、拒绝、错误、超时均不等于授权；不记录提示词全文、密钥、Cookie、私有 URL 或原始推理。MCP API 见 [mcp.md](mcp.md)。

## 结构

主对话是第一级协调者，直接负责目标、决策、阶段记忆、拆分、调度、汇总和验收；执行子 Agent 是第二级，只做分析/实现/测试，不再委派。不创建“协调子 Agent”。

设计初衷：隔离长执行线，让主对话只保留目标、决策、未决问题、下一步和证据路径；执行细节留在子 Agent 和证据文件。它不是缓存清理机制，主对话仍会随轮次增长。

流程：用户 → 主对话（协调/记忆/调度/汇总/验收）→ 执行子 Agent（分析/实现/测试）→ 主对话。

## 开场四选一

同一对话沿用已确认模式。用户明确给出模式则直接记录；模式未确定时先问四选一。只有用户明确变更才重开模式题。开场前最多做一次为确定范围所需的只读查询，运行构建、设备或其他任务操作等开场确认后再做。

1. **快速处理**：主对话直接做，不启子 Agent（纯问答、搜索、小改、简单 Excel、快速查询）。
2. **标准执行**：先问验证范围，再给工作包/计划，然后实现和验证。
3. **长任务**：按交付分阶段，计划前问验证范围；用方案指导降低返工，按需委派并并行执行有实际工作量的任务。同一长任务中的小改和证据更正不重走开场。
4. **只调查**：只读分析，不改文件；范围大时用分析子 Agent。

只给一个按范围/风险/成本动态决定的推荐项。优先真实调用宿主原生提问；其次 MCP 结构化交互；两者不可用则完整文本展示并结束当轮，等待明确回复。推荐勾选不等于确认，取消和拒绝均不采用默认项。快速处理不建子 Agent；只调查只读、范围大可派分析子 Agent，两者不询问验证范围。明确“不用问，直接做”走快速路径，危险操作仍另确认。

3.0 起，长任务在验证范围之后追加唯一的 `codex_gate` 题（`none|closure|packages`，按任务选一个推荐，推荐不是确认）；`standard` 和快速仍按原规则使用 `codex_review`（仅 codex-review 注册已确认时），只调查跳过。长任务不再问 `codex_review`。旧长会话没有 gate 时，首次创建工作包/更新或手动 `project_view_refresh` 返回 `INTAKE_REQUIRED`，补问一次即可，不迁移旧答案。显式 reopen 上游题只 supersede 未完成的下游题；已确认的下游答案保留，但 intake 按当前模式重新判定。

推荐不是固定映射：主对话先看范围/风险/成本，再选一项并说明理由；例如简单只读/小改常快速处理，需改代码或验证常标准执行，跨阶段/长测试常长任务，纯只读定位常只调查；拿不准可推荐标准执行。

### 长任务的委派边界

长任务的目的是让强模型把关关键决策，并行执行缩短交付时间，而不是按角色把每个动作拆开：

- 方案和目标明确就派实施；只有未知的入口、依赖或风险确实挡住下一步时才加侦察。独立验证按已选范围派发；单个阶段被授权、资源或审查阻塞时，推进其他已授权和就绪的分支。
- 工作量较大的检索、实施和测试交给侦察者（`analyze`，只读）、实施者（`implement`，只改 `allowed_files`）、验证者（`test`，只跑命令）；详见 [roles.md](roles.md)。正式审查只由主对话独立派发，子 Agent 不再委派。
- 主对话可直接完成已定位、范围内、低风险的小改、简单只读核对、证据或摘要更正和必要自检，也可把它们并入现有实施任务。不重走开场、不单独建包、不占用别的活动 attempt 的写范围；若改变当前 review subject，仍按原策略复核。实质范围扩大时再划分工作包。
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
- prepare 返回 `attempt_id`、`evidence_dir` 和 leases；子 Agent 在证据目录落盘。
- 生命周期：`PREPARED → LAUNCHING → RUNNING → DRAINING → ENDED`，另有 `UNKNOWN`；`UNKNOWN` 不释放执行槽/资源，也不自动重派。
- 结束用 `work_attempt_update(state="ENDED", result=..., operation_id=...)`；主对话串行提交。`VERSION_CONFLICT` 后重读版本，只补未登记结果。
- 默认并发上限 4；没有宿主真实并发证据时取 1。优先级：用户明确事项 > 关键路径 > 解除阻塞的短任务 > 等待时间。
- 依赖未满足或资源未获得时不启动，换其他就绪任务；失败节点停止其后继，独立分支继续。

### 扇出预算

- 有相互独立且会影响后续决策的未知点时，可并行派发至多 4 个侦察者（`analyze`）；入口明确时直接进入实施，不为凑并发拆分。
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

`work_attempt_update_many(session_id, updates=[{attempt_id,result}], operation_id)`

- 只接受 ENDED 结果，不传生命周期 state；同包且属于当前修订，跨包/跨修订分别返回 `BATCH_PACKAGE_MISMATCH` / `BATCH_REVISION_MISMATCH`。
- 重复 `attempt_id` 或格式错误返回 `INPUT_INVALID`；全有或全无，任一失败整批回滚。
- 一次成功只把工作包 `version` 加一；`operation_id` 必填，重放先于状态/版本检查并返回原回执，不同载荷返回 `OPERATION_CONFLICT`。
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
| 用户旅程冒烟 | 本轮/收尾 | 2–5 min | 浏览器/运行实例 | UI 或用户可见交付必选；记录实例、动作、结果、截图 |
| 定向回归 | 收尾 | 15 min | 板卡 A | 按改动面选择 |
| 全量回归 | 收尾 | 2 h | 板卡 A | 备选 |
| 长时间稳定性 | 收尾 | 8 h | 独占板卡/串口 | 备选 |
| 烧录/擦除 | 按需 | 10 min | 烧录器 | 单独确认 |
| 不做测试 | 本轮/收尾 | 0 | 无 | 与测试及其他不测试选项互斥 |
| 不做测试但记录方法 | 本轮/收尾 | 0 | 无 | 与测试及其他不测试选项互斥 |

- 定向/全量回归和稳定性收尾执行，不每轮重复。
- UI 或用户可见交付必须包含用户旅程冒烟：在用户实际使用的 URL/服务实例上执行「入口 → 主操作 → 可见结果」，记录路由、实例/构建标识、时刻、动作、实际结果、关键断言和必要截图。长驻 dev server 先观察原实例，再在刷新或新页面对照，保留刷新前观测；不可达时记 `NOT_RUN`/`BLOCKED`，不用冷启动测试代替现场验收。
- “不做测试”标记 `NOT_RUN`；“记录方法”只写命令/环境/预期结果到 `AGENT_COORDINATION.md`，不标 `PASS`。
- 每次问只给一个推荐路径；未选中的长耗时操作不执行，新增操作走新修订；烧录/擦除单独确认。
- 未回复不采用推荐项。仅认可宿主真实可观测的超时结果与事先告知的配置策略；本版服务只记录 TIMED_OUT，不自动确认。文本回退结束当轮等待下一轮输入；取消/拒绝/工具失败不授权继续。

## Codex 方案指导与收尾审查

### 高危/长任务方案指导（仅一次）

ZCode 长任务先确认执行模式、验证范围和复核策略，再调用 `Agent(subagent_type="方案顾问")` 做只读指导。无需单独确认这次只读派发。主对话保存真实回执和采纳意见，处理仍待用户决策的问题，然后拆包与派发执行角色。顾问不可用或未完成时，长任务停在方案阶段；`codex_gate` 只决定正式审查频率。ZCode 高危短任务仍可在征得用户同意后使用顾问。以下 Codex 示例适用于其他宿主。

触发：即将修改系统关键配置、删除或覆盖关键数据、做不可逆的系统级操作，或模式为长任务。开工前用宿主原生问答（没有原生工具时文本回退）问一次：**是否先用 Codex 做一次只读项目方案指导？** 推荐使用。用户同意后，主对话运行一次 `codex exec -m gpt-6.2-sol -s read-only -C <项目根> -c model_reasoning_effort=high`，brief 包含目标、计划改动、高危点、约束、验证和回滚；结果纳入方案并记录到项目协作/证据。同一任务只做一次，不重复；Codex 不可用或用户拒绝时说明并继续；只调查、快速/标准且非高危跳过。方案指导不写入 MCP question，不是子 Agent，也不替代收尾审查。

### 长任务复核策略（codex_gate）

ZCode 长任务在验证范围确认后用 `codex_gate` 询问是否做收尾审查，只供选择 `closure` 或 `none`。旧 `packages` 回答只按 `closure` 生效，历史记录保留。以下三档表格和包级门槛仅适用于其他宿主。

| 值 | 含义 | MCP 约束 |
| --- | --- | --- |
| `none` | 不做 Codex 复核或收尾审查 | 不设置 `review_required`；收尾不执行 Codex 审查 |
| `closure` | 仅收尾做一次 Codex 只读审查 | 不设置 `review_required`；收尾按 `codex-review` 执行 |
| `packages` | 每个工作包复核，并在收尾审查整体集成和残余风险 | 新建工作包写入 `review_required=true`；`REVIEW_READY`/`COMPLETED` 需要当前子集的 PASS + ACCEPT review |

- 只有 `packages` 由 MCP 强制包级复核；`closure`/`none` 的收尾审查是 Skill 规则。
- `review_required` 在创建工作包时按当时的 gate 冻结，新修订沿用；旧包缺失该字段按 `false`。
- `packages` 包若没有声明 `review` 子任务，`REVIEW_READY` 明确返回 `REVIEW_REQUIRED`；需在新修订补声明，不能伪造复核结果。
- 旧长会话没有 gate 时（验证范围已确认），`session_get.intake` 为 `CODEX_GATE_REQUIRED`，创建/更新/刷新被 `INTAKE_REQUIRED` 拒绝；补问一次，不回退旧 `codex_review`，也不自动写 `packages`。

### 标准/快速收尾审查（codex_review）

前置条件：`codex-review` 的注册状态可确认已装（本会话技能列表或等效宿主证据确定可用；磁盘文件仅证明已安装；无法确认可用时记 unknown）。满足时，在同一对话首次标准/快速任务的验证范围问完（以及需要时的一次方案指导完成）后追加一问：**收尾是否用 Codex 独立审查？** 后续任务沿用该答案，不重问。用户已明确要求 Codex 审查时不问此问，直接按 `codex-review` 技能执行。长任务不再问 `codex_review`：MCP 在 long 返回 `SKIPPED/USE_CODEX_GATE`。

推荐由 Skill 按项目情况给出单个 `recommendation`：高风险（系统关键配置、删除关键数据、安全相关）、跨仓库或大范围改动、验证不足，或方案指导发现重大风险时推荐 `["yes"]`；大多数普通/小型/已验证任务推荐 `["no"]`。MCP 接受 yes/no，不再强制只推荐 no。

| 选项 | 时机 | 耗时 | 资源 | 说明 |
| --- | --- | --- | --- | --- |
| 不使用 | — | 0 | 无 | 按项目推荐，多数普通任务适用；须明确提交；超时不自动确认 |
| 使用：收尾 Codex 独立审查 | 收尾 | 视范围（一次深度审查约 5–10 万 token） | Codex 配额 | 高风险、大范围或验证不足时推荐；按 `codex-review` 技能执行：只读审查、逐项判定、ACCEPT/REJECT 落档 |

- 未回复不采用推荐项。仅认可宿主真实可观测的超时结果与事先告知的配置策略；本版服务只记录 TIMED_OUT，不自动确认。文本回退结束当轮等待下一轮输入；取消/拒绝/工具失败不授权继续。
- 迟到回复绑定所问问题（四选一/验证范围/codex_gate/codex_review）、工作包与修订号；先核对当前状态，只调整尚未执行的部分，并报告已发生的操作。选“使用”且尚未收尾的，收尾时执行；收尾已完成的标 `NOT_RUN`，要补审走新修订。
- 收尾审查每个任务一次；包级复核按修订的 review attempt 记录，整改后可用新 attempt 重审。已被启动的审查者不再启动新的 Codex 会话；引用或讨论 `codex-review` 技能名不等于调用它。
- 当前会话明确未注册时显示未注册并跳过；未知时显示尚未确认可用并跳过。补登记成功后，只询问尚未确认的审查选择。
- Codex 是外部只读审查者，不是子 Agent；只能由主对话启动，子 Agent 不得启动。方案指导同样只读；审查期间不改源码、不构建/烧录、不碰串口/板卡，结论只认 Codex 最终消息里的 ACCEPT/REJECT。

## ZCode 工作包复核（手动、一次性）

只有用户在当前交互明确点名工作包才启动。先核对宿主实际生效的审阅员工具集与 `zcode-agents/permissions.json`，超出矩阵时停止派发并报告；再根据项目风险、范围和交付阶段询问“本次计划几轮、是否作为收尾审查”并记录。等该包没有活动 attempt，调用 `work_attempt_prepare(subtask_id="review", user_requested=true)`，使用返回的版本和摘要派固定审阅员。每次新增轮次都必须由用户在当前交互明确确认，不自动重试；启动失败或中断消耗当轮，不重置。brief 必须写清受支持入口、输入契约、非目标输入和 findings 格式；审阅员按 `severity`、`reachability`、`blocking_reason` 区分阻塞项与 hardening backlog，只有可达且违反契约/验收条件的问题才进入正常修正。按原生回执规约登记结果。结果只覆盖冻结的对象；是否作为关包条件由用户本轮决定（默认不作为工作包验收条件）。正常验收发现的问题照原标准处理，用户旅程验收和证据核验仍由主对话完成。没有新请求时，恢复旧包后直接核对已完成结果与待办。

## 通用宿主的工作包复核（review 子任务）

下述 `packages` 自动复核和验收门槛仅适用于非原生宿主。`review` 是子任务 `kind`，记录主对话启动的外部 Codex 只读复核。

### 顺序与窗口

1. 非 review 子任务按计划完成（`PASS`/`SUBSTITUTED`），包内没有活动 attempt。MCP 强制复核窗口和子任务声明的 `depends_on`；未声明的完成顺序由主对话保证，后续结果变化会使旧 ACCEPT 变成 `REVIEW_STALE`。
2. `work_attempt_prepare` 单独准备 review attempt；成功返回 `review_subject`。prepare/end review 时其他活动 attempt 返回 `REVIEW_WINDOW_ACTIVE`；review 活动期间也不得准备其他子任务。批量 prepare/update 中 review 必须单独一项/一批。
3. 主对话按 `review_subject` 启动 Codex 只读复核，把严格 JSON manifest 和原始最终消息写入该 attempt 的 `evidence_dir`。
4. `work_attempt_update(state="ENDED", result=...)`：ACCEPT 用 `PASS` + manifest；REJECT 用 `FAIL` + manifest。两者都是合法报告，只有 ACCEPT 映射 PASS。
5. `REVIEW_READY`/`COMPLETED` 检查 review 子任务的最新结果：需要 `PASS`、verdict=ACCEPT 且 subject 等于当前值；无 → `REVIEW_REQUIRED`，旧 → `REVIEW_STALE`。`COMPLETED` 和 `handoff_export` 还会重算 subject。
- `work_package_update(results=...)` 直接写 review 子任务 → `REVIEW_ATTEMPT_REQUIRED`；必须经 attempt 绑定 subject。
- 旧 ACCEPT 不能复用；文件、定义或非 review 结果变化后用新 review attempt 重审，最新结果决定状态。
- Codex 不可用/超时/输出残缺：review 不能 `PASS`，保持 `REVIEW_REQUIRED`；不得伪造 ACCEPT。
- `force_close` 是操作员例外，不表示 review-accepted。

### 复核退回与证据复用

- 先报告已核实的交付和未完成的复核。区分产品缺陷、证据错误、非阻塞建议、工具或资源问题；只修当前验收范围内有证据的阻塞项。建议可列为后续项，不自动扩大本轮范围。工作包应声明受支持入口、输入契约和非目标输入；findings 带 `severity`、`reachability` 和 `blocking_reason`，只有“可达且违反契约/验收条件”的问题才作为阻塞项，构造性或不可达输入列入 hardening backlog。
- `allowed_files` 从建包时就限定在实际改动和必要的审查文件；不把 `build/`、日志、缓存和备份等产物放进源码快照。遇到 `REVIEW_SUBJECT_TOO_LARGE`，修工作包范围；不因审查范围收窄而自动重构建、重烧录或重复跑硬件验证。
- 只纠正日志摘要或工作包元数据时，保留原件；核对代码／产物、设备状态及测试条件没有受影响，才可引用可核实的原始证据。如果新修订要求证据位于当前目录，按现有接口在当前证据目录归档副本或证据复核记录，注明原修订、原路径、哈希、原运行时间和复用理由，不改成“本轮重跑”。有变化、证据不足或接口不接受时，只补必要的验证；不能证明的项不标 PASS。不复用旧审查 ACCEPT。
- 收尾或非原生宿主的包级 `REJECT` 后，可对明确的范围内问题自动做一轮「修正 → 必要验证 → 复审」。每个修订默认最多两次自动复核派发（含中断和无效回执）；ZCode 的手动包级复核不适用此规则，每包最多一次。到限聚合全部 findings 交用户决策，不自动创建下一轮。仍受阻、同因反复、无新证据或需扩大范围时，只停受影响工作，报告交付现状、剩余阻塞和下一项决策；不自动创建下一轮。这不放宽 `review_required`。收尾审查在已有包级复核时聚焦集成和残余风险，不逐包重审。增量复核仍须签发绑定当前完整 subject 的新回执；只有基线、契约、依赖和证据变化可核清时才缩小检查范围，并计入原预算。

例：编译通过，用户自行烧录后开机日志也通过，但归档把「4 段字节一致，另 1 段按策略跳过」写成「5 段一致」。直接据原始日志改归档；需要正式复核时，只复审更正后的证据，不把这当成固件失败或要求再刷一次。

### 证据 manifest

严格 JSON 对象，字段集恰好：

`verdict`（`ACCEPT`|`REJECT`）、`session_id`、`model`、`subject_sha256`、`final_message_sha256`、`command`、`exit_code`（int 且为 0）、`raw_path`。

- `session_id`/`model`/`command` 为非空字符串（上限 256/128/4000）；`subject_sha256`/`final_message_sha256` 为 64 位小写十六进制。
- `subject_sha256` 必须等于 prepare 返回的 `review_subject`，否则 `REVIEW_STALE`。
- `raw_path` 为相对 manifest 目录的路径，不得绝对路径、反斜杠或 `..`；解析后必须仍在本次 attempt 证据目录。
- 原始最终消息必须存在、sha256 匹配、UTF-8，且恰好一行结论 `结论: ACCEPT` 或 `结论: REJECT`（支持 `**...**` 和中文冒号），与 `verdict` 一致。
- review 忽略 `required_content`；不要用正文子串作为判据。

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
- 排除树（`.git`/`.venv`/`.venv-mcp`/`.scs`/`.archify` 和 `<docs_root>/agent/evidence`）内的链接直接跳过、不记录。
- fifo/socket/设备等非 regular/dir/symlink 的特殊文件返回 `REVIEW_SUBJECT_INCOMPLETE`。
- `allowed_files` 中的 `./` 和重复分隔符按路径部件归一化后遍历、去重；绝对路径、`..` 和反斜杠仍被拒绝。

排除 `.git`/`.venv`/`.venv-mcp`/`.scs`/`.archify` 和 `<docs_root>/agent/evidence`；上限 500 条目/100 MB（链接也计入条目上限），超限 `REVIEW_SUBJECT_TOO_LARGE`；读取失败/不可读 `REVIEW_SUBJECT_INCOMPLETE`。摘要使用稳定排序，证据目录变化不影响结果；其他在 `allowed_files` 内的文件或链接变化会改变 subject，并在下一次重算表现为 `REVIEW_STALE`。

## 破坏性操作闸门

适用于所有模式（快速处理、标准执行、长任务；只调查如实际执行也适用），与是否启用 Codex 审查无关。

### 触发分类

必须走闸门：

- 删除、覆盖、移动用户数据或系统关键配置；
- 整片擦除或高风险刷写；不满足下列日常开发板烧录条件的刷写同样走闸门；
- `drop database` / `drop table` 等破坏性数据库操作；
- 不可逆 git 操作（`reset --hard`、`clean -fdx`、强制推送等）；
- 递归 `chmod` / `chown`；
- 卸载关键系统/运行时包；
- 在 `allowed_files` 之外的大范围改写。

不走闸门：普通代码编辑、证据写入（`<docs-root>/agent/evidence/`）、`project_view_refresh` 视图刷新、只读调查，以及满足以下条件的日常开发板烧录。

### 烧录按实际写入范围分类

**日常开发板烧录须同时满足**：能核实连到用户指定的开发／测试板；按实际命令、参数、构建产物和分区信息核实只更新应用固件分区（包括 app 分区自身必要的扇区擦除）；有已知可用的固件和回刷方式；不会改写引导程序、分区表、OTA 状态、安全配置或清除／迁移持久数据。任何一项不明，先做只读核查；仍不能满足则按高风险处理。适用的项目或宿主更严格限制照常遵守。

- **授权仍必需**：主对话出示目标板、拟执行命令、写入范围及回刷方式，执行前取得用户对这次操作的明确授权，并在会话或证据中保留。不把验证范围、默认选项、沉默或笼统的「继续」当成授权；目标、命令或写入范围变化应重新确认。满足日常条件时，不派操作预审、不调 `operation_gate_record` 或 `operation_gate_check`，也不伪造 `APPROVED`。这不影响已选的包级或收尾审查。
- **完整闸门**：整片擦除、写 bootloader／分区表／OTA 状态（含 `otadata`）／安全配置、可能丢失持久数据、刷生产设备，或目标、可能写入范围或回滚不明确。按命令实际可能写的范围判断；差异烧录预计跳过某段不足以证明不会写该段。同一被 REJECT 的方案不得改称日常烧录继续执行。
- **例子**：指定开发板上仅重刷可回滚的 app、且保留原 NVS 和文件系统 → 仅用户明确授权；同一条 `flash` 可能写 bootloader、OTA 状态和 app，即使预计跳过 storage → 完整闸门；串口接的是哪块板或脚本到底会刷哪些段不清楚 → 不按日常烧录执行。

### 完整闸门流程

1. 主对话准备：精确命令行（或 argv 拼接）、绝对 `cwd`（必须在项目根内）、目标列表（项目相对路径或 `device:`/`serial:`/`programmer:` 逻辑 ID）、影响面、备份、回滚方案，以及可选的 `baseline`（`{"head": "<sha>"}` 或 `{"files": {path: sha256}}`）。
2. 需要时先做 Codex 只读预审。报告必须落项目内文件，正文最终结论有且仅有一行 `结论: ACCEPT` 或 `结论: REJECT`（允许 `**结论: ACCEPT**`）；报告以登记时的 sha256 固化。
3. 调用 `operation_gate_record` 登记决定：
   - `ACCEPT`：报告结论必须是 ACCEPT，且 `user_confirmed=true`；否则 `GATE_INVALID`。
   - `REJECT`：报告结论必须是 REJECT；同一 `operation_id`/digest 之后是终态，任何 ACCEPT 返回 `GATE_REJECTED`。
   - `UNAVAILABLE_OVERRIDE`：仅 Codex 不可用时使用；必须 `user_confirmed=true` 且原因不少于 10 字，可附失败/残缺报告；`operation_gate_check` 永远返回 `UNAVAILABLE_OVERRIDE`，不得当作 APPROVED。
4. 执行前调用 `operation_gate_check`，只有 `status=APPROVED` 才继续；同时仍要保留用户的独立确认。命令、`cwd`、目标或 `baseline` 任一变化都会得到 `STALE`，必须重新登记。

### 绑定与幂等

- `operation_digest = sha256(canonical JSON{command, cwd, targets 排序, baseline})`。目标按排序后的集合比较，顺序不影响结果；`baseline` 省略时按 `null` 参与 digest。
- 记录按 `session_id` + `operation_id` 归属；换会话后旧记录不可见，需在原会话登记或重新登记。
- 同 `operation_id` + 同 digest + 同 verdict 返回原记录；同 `operation_id` 不同 digest 返回 `OPERATION_CONFLICT`。已有 REJECT 后不得写 ACCEPT。
- 事件只存有界摘要（digest、command、cwd、targets、impact、backup、rollback、verdict、report_path、report_sha256、user_confirmed、reason、baseline、at），总长不超过 16 KB，不存报告全文。

### 不可绕过

- `REJECT` 不可绕过；需要改方案时使用新的 `operation_id`，不得覆盖原记录。
- Codex 超时、INCOMPLETE、输出残缺、报告缺失或结论行不唯一，都不得放行，也不得记为 ACCEPT。
- Codex 不可用时只能记 `UNAVAILABLE_OVERRIDE`：必须用户明确二次确认并写明原因；该状态永远不等同于 `APPROVED`。
- `operation_gate_check` 的 `APPROVED` 只表示“存在当前命令/目标/基线对应的 ACCEPT 记录且用户已确认”，不是执行授权本身。

### 诚实边界

MCP 不执行宿主命令，也没有执行前 Hook，无法拦截真实命令。没有宿主 Hook 时，本节是协议级约束（`PROTOCOL_ONLY`），不是执行级强制；不得宣称已实现内核级拦截或命令执行拦截。宿主若提供 Hook，应在执行前调用 `operation_gate_check` 并以 `APPROVED` 作为放行条件，同时保留用户确认。

## 工作流程

1. 按适用条件确认模式、验证范围及复核策略。ZCode 长任务实际完成一次 `方案顾问` 指导后才拆工作包；其他宿主沿用原 Codex 指导。收尾按已确认的复核策略执行。
2. 主对话定义工作包：目标、验收、目录、基线、允许/禁止操作、子 Agent 类型、证据要求；`packages` 包声明 `review` 子任务。
3. 主对话负责 SQLite 与可审查 Markdown 的显式同步；服务只初始化缺失文件，不覆盖手工记录。断开 MCP 后纯 Skill 可维护文件；恢复时先核对，不盲目导入。主对话读写默认记忆：`<docs-root>/agent/AGENT_PROJECT.md`、`<docs-root>/agent/AGENT_COORDINATION.md`、`<docs-root>/agent/evidence/`。
4. 仅拆分能独立推进的子任务。入口不明时按需并行调查，实现按 `allowed_files` 两两不相交并检查资源独立性（默认上限 4，无宿主并发证据取 1）。同文件、板卡、串口或烧录器串行，包级 review attempt 在本包中单独运行。
5. 用 `Agent` 工具创建执行子 Agent，按侦察者/实施者/验证者写 prompt，含子任务、范围、禁止、检查、证据路径、“不得再委派/不得启动 Codex”。
6. 子 Agent 执行并落证据；按 [roles.md](roles.md) 返回状态、动作、证据路径、首异常、剩余问题、资源释放，不贴原始日志。
7. 主对话汇总：核对证据，区分 `PASS/FAIL/BLOCKED/NOT_RUN/SUBSTITUTED`，更新协作记录；`packages` 包在 `REVIEW_READY` 前完成 review ACCEPT（subject 当前）。证据按 `kind` 区分 test/build/lint/smoke，绑定的源快照、argv/cwd、日志和产物由主对话核对。
8. 主对话独立验收 diff/日志/测试/产物，并完成 UI/用户可见交付的用户旅程验收；只有主对话更新验收结论。
9. 同一工作包继续在主对话；压缩/重启时从 `AGENT_COORDINATION.md` 恢复。

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
| `<docs-root>/agent/evidence/WP-<id>/R<rev>/<subtask-id>/` | 子 Agent/主对话 | 完整日志、测试输出、截图、报告 |

- 三级记忆：主对话摘要 → 协作记录 → 证据文件；主对话只读关键证据。
- 子 Agent 回传有界（建议 ≤1–2 KB），不贴原始日志。
- 每个工作包/阶段结束写 handoff；主对话接近 60%–70% 上下文或压缩时，新开会话，从文件恢复。
- 长测试后台化：子 Agent 返回 PID/日志/状态/停止方式后结束，主对话按检查点查询。
- 子 Agent 上下文过大时停止并新建，传摘要和证据路径。
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

工作包终态：主对话验收通过后用 `COMPLETED` 关闭；重复、被取代或不再需要的用 `ABANDONED` 关闭。终态必须带 `close_reason`，服务写入 `closed_at`、`closed_by_session` 和事件记录，之后不可修改；后续工作新建工作包。用户叫停、等待决策或复核预算耗尽时，用可恢复的 `SUSPENDED` 暂停，记录 `reason`、`resume_condition` 和 `known_limitations`；暂停不等于验收通过，必验项、操作授权和已选复核继续按原条件判定。主对话用 `work_package_update`；桌面管理器用本地操作员工具 `work_package_close`，从包内读取 owner session，不能借此跳过版本或原因校验。阶段收尾时核对长期 `PENDING/IN_PROGRESS` 的滞留包和重复包，按实际情况关闭，避免记录一直挂在“待开始”。桌面管理器默认隐藏终态，可用“显示已关闭”查看。

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
11. 已确认的模式在本对话沿用，新的会话单独确认或记录。
12. 验证范围仅本对话首次标准/长任务确认；未选项不执行，推荐不是授权。
13. 每个对话在模式不明确时问一次；用户明确选定模式时直接登记，后续沿用。
14. 收尾审查仅明确已注册 codex-review 才问；未知/未装跳过；取消、拒绝、错误、超时保持未确认。长任务改用 `codex_gate`，标准/快速保留 `codex_review`。
15. 生成的正式文档按 `00_doc/` → `00_docs/` → `docs/` → `doc/` → 新建 `docs/` 的顺序落盘；协议记忆、审查报告、证据保持既有路径，不改道。
16. 四选一和验证范围各只标一个推荐，且由主对话在必要只读调查后按范围/风险/成本选择，不固定同一项；推荐不免问。
17. 用户明确要求改变执行方式或验证范围时，重开所需项目并按该答复更新。
18. `review` 子任务由主对话单独启动。ZCode 使用 `固定审阅员`，其他宿主使用 Codex 只读复核。
19. 按实际回执记录 review ACCEPT、gate ACCEPT 或 `UNAVAILABLE_OVERRIDE`；审阅员不可用时保持未通过。
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
  subagent_type="general-purpose",
  run_in_background=false,
  prompt="""
你是工作包 WP-<id> / R<n> 的执行子 Agent，角色：<侦察者 analyze / 实施者 implement / 验证者 test>。
主对话负责目标、阶段记忆、调度和验收；你不创建下一级 Agent，不启动 Codex。
工作目录：<cwd>
子任务：<...>
输入上下文：<...>
允许范围：<文件/设备/操作>
禁止事项：<...>
基线：<...>
检查方法：<...>
证据写入：<docs-root>/agent/evidence/WP-<id>/R<n>/<subtask-id>/<attempt-id>/
最终消息：状态、实际动作、证据路径、首异常、剩余问题、资源释放（按 roles.md schema）。
"""
)
```

模型：ZCode 的注册角色使用自身的固定或继承模型设置。其他宿主按各自模式传递 provider/model。

## 检查清单

- [ ] 按适用条件完成提问：模式未确定时询问，已有明确模式直接登记；同一对话首次标准/长任务问验证范围；长任务问并确认 `codex_gate`（通常推荐 closure），标准/快速按 codex-review 注册状态问 `codex_review`，只调查跳过；ZCode 长任务的方案顾问是否在拆包前实际完成一次；明确“不用问，直接做”/明确要求审查等例外已处理。
- [ ] 快速/只调查按各自范围执行；标准执行按计划实现/验证；长任务按需使用已注册的执行角色和并行预算。小改和证据更正按本包处理，子 Agent 无下一级委派。
- [ ] `packages` 包声明了 `review` 子任务；review attempt 独立运行，ZCode 仅派 `固定审阅员`；`REVIEW_READY` 前的 PASS review 带当前 subject 的 ACCEPT manifest；`REVIEW_STALE` 已用新 attempt 重审。
- [ ] 批量 prepare/update 单包原子；`work_attempt_update_many` 的 `operation_id` 已提供；`work_attempt_note` 未被当作存活证明；`run_map` 有界且未写文件。
- [ ] 非 implement 未声明文件读租约；旧包遗留 read 资源已被过滤并提示；同文件/同设备仍串行。
- [ ] 未选中的长耗时操作没有进入计划；破坏性操作已按「破坏性操作闸门」登记，并在执行前 `operation_gate_check` 为 `APPROVED`；REJECT/UNAVAILABLE_OVERRIDE 未被当作放行。
- [ ] 证据已落盘，主对话检查了关键证据和实际产物；UI/用户可见交付完成了用户旅程验收（现场实例、动作、可见结果、截图），无证据不标 PASS。
- [ ] 子 Agent 实际工具集符合 `zcode-agents/permissions.json`；超出矩阵的角色未派发或已按真实原因记录。review findings 带 severity/reachability，构造性输入未阻塞放行。
- [ ] 阶段结束写了 handoff；用户叫停/等待决策/预算耗尽时包为 `SUSPENDED`（含 reason、resume_condition、known_limitations），未伪装成 COMPLETED/ABANDONED；上下文接近上限时新会话恢复。
- [ ] 正式文档落在 `00_doc/`（或回退 `00_docs/`/`docs/`/`doc/`）；协议记忆、审查报告、证据未改道。


## Obsidian 项目管理

用户启用时以 docs-root 为独立库根，agent/acceptance/ 是结构化工作包的可重生成浏览视图；源记忆和证据保持原职责。project_view_refresh 生成页面并可开启 MCP 工作包变更后的自动刷新；project_view_info 只读检查并返回打开链接。批注保留区之外的编辑冲突时停止视图覆盖，已保存的工作包不回滚。无需社区插件，不改全局库注册；首次打开库需宿主/用户完成。详见 [obsidian.md](obsidian.md)。
