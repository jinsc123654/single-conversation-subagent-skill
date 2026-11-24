# MCP 用法与恢复

纯 Skill 无 Python/MCP 依赖。混合模式只在工具可见时使用；可见不代表模型一定调用，也不证明 UI 显示。服务无原生 Agent 调度工具。

ZCode stdio 服务配置环境 `SCS_HOST=zcode`、`SCS_QUESTION_CHANNEL=native`、`SCS_NATIVE_REVIEW=1`，入口为 `python -m scs_mcp --project-root /absolute/workspace`。配置根是授权边界，每个任务项目维护独立的 `.scs/state.sqlite3`。SQLite WAL，schema 3，busy timeout 3 秒；时机和路由用问题记录，破坏性闸门用事件记录。只支持本地单用户项目；不提供认证或跨用户服务。若可用 MCP 未启用 `native_review`，不按旧复核协议提交；请修正服务配置或按本页纯 Skill 回退。

## 初次交互

日常任务可直接使用宿主 Agent。长任务或明确需要持久化协作状态时，使用 `session_open(project_root=<任务项目根>, conversation_id=<真实对话 ID>)`。项目根位于配置允许的工作区内。`client_name` 可省略；跨宿主或不同 client_name 使用独立命名空间。宿主未提供对话 ID 时省略该字段，保留返回的 `session_id` 用于续接。

1. 在 ZCode 4 中，`session_open`、`interaction_request`、`interaction_record`、`interaction_submit` 使用逐工具的 `anthropic/alwaysLoad` 元数据。已经可见时直接调用。新会话模式明确时传 `initial_mode`；长任务须用户明确选择。`initial_mode` 不改变已有记录，不与 `explicit_skip` 一起使用。Obsidian 登记只在需要且获准时启用。
2. 不明确的模式及首次正式复核前缺失的审阅员，可用 `interaction_request(...,channel="mcp")`。MCP 表单可用时，同一次调用保存单选答复；退回原生或文本时用原 `question_id` 提交。`interaction_ask` 的 15 秒是独立的服务端截止时间。
3. 长任务卡片确认验证范围和复核策略后，用一次 `interaction_record(session_id=..., selections={"verification":["smoke","targeted"],"codex_gate":"packages"}, source="native")` 写入。已有明确文本答复时用 `source="text"`；可只传未记录的项，也可传 `mode` 或 `review_route`。批次原子保存，同一答复重试幂等。已有待答题号通过 `interaction_submit` 完成；已确认答案的变更通过 `interaction_reopen`。
4. `session_get` 返回已确认选择、待答题号、`review_route` 和 `intake`。模式变更须用户明确要求；reopen 后使用 `interaction_record` 保存明确文本答复。只有实际答复可以写入 `CONFIRMED`。

## 问题顺序、复核时机与 intake

| 模式 | 验证范围 | 使用 ZCode Agent |
| --- | --- | --- |
| quick | 按任务范围直接处理 | 主对话处理，按用户请求使用指定角色 |
| standard | 说明验证计划，按成本和范围决定是否询问 | 按需直接使用侦察、实施或验证角色 |
| long | 一张卡片确认验证和 `codex_gate` | 走工作包、执行角色和所选独立复核 |
| investigate | 只读 | 按需直接使用 `查询侦察员` |

验证值为 `smoke/targeted/full/stability/none/method-only`；`none` 和 `method-only` 各自排除其他项。`codex_review` 保留为旧服务字段。`intake.ready` 表示当前模式可进入执行阶段，原生长任务需要验证和复核策略。审阅员路线可在实际复核前补记。

`codex_gate` 值：

| 值 | 含义 | MCP 行为 |
| --- | --- | --- |
| `none` | 不做包级或收尾审查 | 不设置 `review_required` |
| `closure` | 仅收尾一次原生只读审查 | 不设置 `review_required`；收尾由 Skill 执行 |
| `packages` | 每个工作包复核 + 收尾审查（角色可用时推荐） | 新包 `review_required=true`；`REVIEW_READY`/`COMPLETED` 需当前 subject 的 PASS ACCEPT review |

- 推荐不是确认。原生 Reviewer 不可派发时说明限制并推荐 `none`，不把 `closure` 当替代。long 的 `codex_review` 返回 `SKIPPED/USE_CODEX_GATE`。
- 旧长会话缺 gate 时，`session_get.codex_gate=null`，`intake.ready=false`、`reason=CODEX_GATE_REQUIRED`；创建/更新或手动 `project_view_refresh` 可返回 `INTAKE_REQUIRED`。补问当前缺项，不迁移旧 `codex_review` 或自动确认。
- 第一次正式包级复核、收尾复核或操作预审前，确认 `review_route`。`gpt` 派 `固定审阅员`，`inherit` 派 `继承审阅员`。已有明确路线沿用，角色不可用时暂缓该复核和依赖它的放行。准备原生审查 attempt 或登记操作预审仍要求真实路线；缺项返回 `REVIEW_ROUTE_REQUIRED`。普通工作包、执行 attempt 和视图在验证和复核策略已确定后可先开始。
- 在旧服务上，`INTAKE_REQUIRED` 仍可能要求提前确认 `review_route`。核对原因，仅为受影响的操作补回适用项。旧版 `standard` 可能要求验证范围才允许创建协作记录，日常 Agent 委派可继续按任务权限使用宿主。
- `方案顾问` 由用户明确请求时独立派发，只给建议。破坏性操作仍要求独立用户确认、真实原生预审及当前 `APPROVED`。

## 恢复会话与补登记

传 `session_open(project_root=<原项目绝对路径>, resume_session_id=<原session_id>, review_registration="registered")`，沿用首次调用的 client_name（原来省略则仍省略）。省略 conversation_id、explicit_skip 和 initial_mode。只在当前宿主确认至少一个合格原生 Reviewer 可派发时补登记为 registered；unknown 保留已有确定状态。服务恢复同一会话 ID 和选项；项目或宿主不匹配返回 SESSION_NOT_FOUND。此路径也用于 MCP 进程重启后的临时会话重连。恢复不改变已确认的时机或路由，也不将其复制到新对话。

## 问题与故障

旧版逐题接口以 `interaction_submit` 返回 `CONFIRMED` 作为完成回执。`QUESTION_PENDING` 返回原题号、类型和状态；适用题使用真实答复提交原题，旧服务的不适用复核题按上文兼容规则处理。仅长任务确认复核时机和适用路由；已启用的收尾审查在实现和所选验证结束后才派发。方案指导、包级复核和操作预审各按自己的时机执行。

`interaction_submit` 接受 accept/decline/cancel/timeout/unsupported/error。accept 必须有明确合法选择。timeout 需要宿主可观测超时回执 `observed_timeout=true`，仍不自动选项；`interaction_ask` 的服务端截止时间即该回执，超时返回推荐项供主对话继续，不写 `CONFIRMED`。RPC 超时提交 error，不能填 timeout。decline/cancel/timeout 保持终态未确认。

重试或用户明确改变时用 `interaction_reopen(session_id,kind,explicit_user_change=...)`；活动/已确认问题必须有用户明确变更。然后 request 获取新 question_id，旧回复返回 LATE_REPLY。RPC 异常会把问题标为 `FAILED`，需显式 reopen 后重试；无 form/modern 回落不标 terminal，保持 `WAITING_USER`，直接按同一 question_id 提交文本/原生答复。客户端重启使 SDK 密封 requestState 失效时，核对 session_get 的未决题，显式 reopen，再请求；不要复用旧响应。

服务可能观察 REQUESTED/WAITING_USER，不能证明已渲染；统一 UI_NOT_OBSERVABLE，真实接受回执只证明客户端提交了内容。模型可能未调用工具，客户端也可能自动生成答案；最终应以宿主实际用户交互记录核对。

`diagnostics_check(session_id=...)`：版本、DB quick_check、工具注册（当前 39 个）、协议、声明能力、最近 30 条脱敏事件。SKILL_NOT_LOADED 需宿主加载证据；TOOL_NOT_CALLED 需宿主调用日志。RPC_ERROR 与 ELICITATION_UNSUPPORTED 可在服务观察；用户取消/拒绝/接受分别 USER_CANCELLED/USER_DECLINED/USER_CONFIRMED。stdout 仅协议；默认错误日志 stderr，不启用 SDK 调试日志以免写入调用输入。

## 工作包、证据与租约

标准/快速的内建任务不要求 SCS 工作包。以下是可选工作包的 API 示例：

```json
{"session_id":"S-...","goal":"修复解析器","allowed_files":["src/parser.py"],"acceptance":["空输入不异常"],"request_key":"parser-1","subtasks":[{"id":"T1","kind":"test","allowed_files":["src/parser.py"],"acceptance":["定向用例通过"]}]}
```

`packages` 长任务包应声明一个 review 子任务：

```json
{"session_id":"S-...","goal":"修复解析器","allowed_files":["src/parser.py"],"acceptance":["空输入不异常","复核 ACCEPT"],"request_key":"parser-r1","subtasks":[
  {"id":"T1","kind":"implement","allowed_files":["src/parser.py"],"acceptance":["空输入不异常"]},
  {"id":"R1","kind":"review","allowed_files":["src/parser.py"],"acceptance":["原生只读复核 ACCEPT"],"depends_on":["T1"]}
]}
```

传给 work_package_create，返回唯一 WP ID、revision=1、version=1、docs_root、`review_required` 和待同步记录路径。request_key 在同对话内幂等；不同定义同键冲突。子任务 `kind` 仍为 `analyze` / `implement` / `test` / `review`；长任务自动派发的 ZCode `Agent.subagent_type` 分别用 `查询侦察员` / `实施工程师` / `测试验证员` / 锁定路由对应的 `固定审阅员`（`gpt`）或 `继承审阅员`（`inherit`）。文件范围为明确相对文件/目录，不是 glob；子任务不可扩大父范围。服务不授予文件或执行权限。

`work_package_update` 需 expected_version，成功加一，旧版本 VERSION_CONFLICT；不要盲重试。状态 PENDING → IN_PROGRESS → REVIEW_READY，BLOCKED 可恢复为 IN_PROGRESS；验收通过用 COMPLETED、重复/被取代用 ABANDONED 关闭，终态必须带 close_reason 且不可修改。`packages` 包在 REVIEW_READY/COMPLETED 需要 review，`REVIEW_REQUIRED`/`REVIEW_STALE` 分别表示缺少和过期；`work_package_update(results=...)` 直接写 review 子任务返回 `REVIEW_ATTEMPT_REQUIRED`。桌面管理器关闭滞留/重复包时调用 `work_package_close(project_root, package_id, expected_version, state, close_reason)`；它是本地操作员路径，仍从包内读取 owner session 并写审计，主对话仍应优先用 `work_package_update`。`force=true` 是操作员例外，`force_closed` 不表示 review-accepted。

### 并发 attempt

- `work_attempt_prepare(session_id, package_id, expected_version, subtask_id, host_agent_id?=None, ttl_seconds=120)`：校验依赖已 `PASS`/`SUBSTITUTED` 且无活动 attempt，原子占用子任务声明的完整资源集合，建立 `.../R<rev>/<subtask>/<attempt>/` 证据目录；返回 `attempt`、`leases`、`evidence_dir`、`warnings`。成功后才调用宿主 Agent。工作包 version 不变。
- `work_attempt_prepare_many(session_id, package_id, expected_version, subtask_ids, host_agent_ids?=None, ttl_seconds=120)`：单包单事务批量 prepare，全有或全无；成功后 version 不变；返回 `attempts[]`。重复 ID 返回 INPUT_INVALID，缺失子任务 SUBTASK_INVALID；同批依赖按波次；含 review 且多于一项返回 REVIEW_WINDOW_ACTIVE。`host_agent_ids` 可选，提供时与 `subtask_ids` 一一对应。
- `work_attempt_update(session_id, attempt_id, expected_version, state?, result?, host_agent_id?, operation_id?)`：`PREPARED → LAUNCHING → RUNNING → DRAINING → ENDED`，另有 `UNKNOWN`；`ENDED` 写入子任务结果并让工作包 version+1，`PASS` 必须有 attempt 目录内可验证证据；`UNKNOWN` 不释放资源、不自动重派。`operation_id` 同载荷重试返回原回执。
- `work_attempt_update_many(session_id, updates=[{attempt_id,result}], operation_id, expected_version?=None)`：只接受 ENDED 结果；同包当前修订；全有或全无；成功只 version+1；`operation_id` 必填且重放先于状态/版本检查（相同载荷重放原样返回历史回执，含已记录的 `view_update`）；可选 `expected_version`（非 bool 整数）开启乐观锁，在重放与 `PACKAGE_CLOSED` 检查后校验版本，不匹配返回 `VERSION_CONFLICT`（修改 `expected_version` 复用同 `operation_id` 返回 `OPERATION_CONFLICT`）；review 不能与其他项混批。跨包/跨修订返回 BATCH_PACKAGE_MISMATCH/BATCH_REVISION_MISMATCH。
- `work_attempt_list(session_id, package_id, revision?)`：只读查询 attempt 生命周期、宿主、结果和资源。
- `work_attempt_note(session_id, attempt_id, note, note_id)`：追加审计备注，note 1..2000 字、note_id 为简单标识；不写版本、不续租、不证明进程存活；同 note_id 同载荷幂等，不同载荷 OPERATION_CONFLICT。
- 子任务定义支持 `depends_on`、`resources`（`file:`/`device:`/`serial:`/`programmer:` + `read`/`write`；3.0 文件只支持 `write`）和 `required`。目录资源覆盖后代。
- **读租约限制（3.0）**：只有 implement 默认对每个 scope 取独占写租约；analyze/test/review 默认 `[]`。显式 `file:` + `read` 返回 `RESOURCE_READ_UNSUPPORTED`；设备类 read 未禁用。旧包已存的文件 read 资源在 prepare 时过滤并返回 `warnings[]`（`READ_LEASE_SKIPPED`），不阻断。本版没有共享文件读保护。
- review attempt 必须单独运行：prepare 时包内不得有其他活动 attempt；review 活动期间不得 prepare 其他子任务；批量中 review 必须单独一批，否则 REVIEW_WINDOW_ACTIVE。
- **原生 `host_agent_id` 全局单活互斥与运行期不可换绑**：开启 `SCS_NATIVE_REVIEW=1` 时，`work_attempt_prepare` / `work_attempt_prepare_many` / `work_attempt_update` 强制同一个 `host_agent_id` 在全局所有工作包中同时最多只能有一个非 `ENDED` 的活动 attempt，冲突返回 `HOST_AGENT_ACTIVE`；活动 attempt 一旦绑定 `host_agent_id`，运行期禁止换绑为其他 Agent ID（原生 review attempt 返回 `REVIEW_AGENT_MISMATCH`，其余原生 attempt 返回 `HOST_AGENT_MISMATCH`）。
- `REVIEW_READY`/`COMPLETED`/`ABANDONED`/`new_revision`/`handoff_export` 前必须有静止屏障；有活动 attempt 时 `work_package_update` 不接受直接子任务结果。结果 PASS/FAIL/BLOCKED/NOT_RUN/SUBSTITUTED；PASS 是子任务证据报告，不是最终接受。new_revision=true 单独调用，清空结果并分配新证据目录，不删除旧文件；`review_required` 沿用。

### review 子任务与证据

1. 非 review 子任务完成且包内无活动 attempt；`work_attempt_prepare` 单独准备 review attempt，返回 `review_subject`；`input_snapshot` 锁定当前 `review_route`。
2. 主对话准备范围、基线、有界 diff/哈希和证据索引，按锁定路由调用 ZCode `Agent`，`gpt` 派 `固定审阅员`，`inherit` 派 `继承审阅员`。生命周期更新中一旦宿主实际报告 ID 即可登记 `host_agent_id`；不得预填或伪造，完成时以真实宿主完成回执核对同一 ID。主对话在该 attempt 证据目录保存原始最终消息和 v2 manifest。
3. 用单个 `work_attempt_update(state="ENDED", host_agent_id=<实际 ID>, result={"status":"PASS","evidence":[...manifest...]})` 记录 ACCEPT；REJECT 用 `FAIL` + manifest。manifest 的 `agent_id` 必须与 `host_agent_id` 一致。不要用批量结果代替带真实 ID 的单 attempt 提交。
4. `REVIEW_READY`/`COMPLETED` 检查 PASS + ACCEPT 且 subject 当前；`COMPLETED` 和 `handoff_export` 重算 subject，不一致返回 `REVIEW_STALE`。未声明 review 子任务时返回 `REVIEW_REQUIRED`。

原生 review manifest 是严格 JSON 对象，字段集恰好：

```json
{
  "schema_version": 2,
  "review_subject": "<prepare 返回的 review_subject>",
  "agent_type": "固定审阅员",
  "agent_id": "<实际宿主 Agent 完成 ID>",
  "model": "unobserved",
  "status": "completed",
  "verdict": "ACCEPT",
  "final_message": {"path": "final-message.md", "sha256": "<原始最终消息的 SHA-256>"}
}
```

- `agent_type` 必须是锁定路由对应的派发 ID：`gpt` → `固定审阅员`，`inherit` → `继承审阅员`。`方案顾问` 的建议不能提交为复核证据。`model` 是宿主可观测的 `provider/model`，未暴露时写 `unobserved`，不猜测。`status` 只有实际完成才填 `completed`。不要加 shell 命令或伪造退出码。
- `review_subject` 须等于 prepare 返回值；不匹配为 `REVIEW_STALE`。`final_message` 恰好含 `path`、`sha256`；path 相对 manifest 目录，须保持在 attempt 证据目录内，不得绝对路径、反斜杠或 `..`。
- 原始最终消息必须存在、sha256 匹配、UTF-8，且恰好一行 `结论: ACCEPT` 或 `结论: REJECT`（允许 `**...**` 和中文冒号），与 verdict 一致。不得拼接、补写或只查子串；review 忽略 `required_content`。
- REJECT 合法且以 FAIL 落账，整改后用新 attempt。角色不可用、超时、缺实际 ID 或输出残缺时不提交伪 manifest、不标 PASS。只有原生 v2 证据用于本流程。

`review_subject` 覆盖修订定义（goal/allowed_files/acceptance/非 review 子任务，按 id 排序）、非 review 子任务的 status+evidence(path,sha256)、包 allowed_files 的有界快照：regular 文件 path→sha256；范围内每个符号链接记录相对路径→`readlink` 目标的原始字节的十六进制（有链接时才出现 `links` 字段），不跟随、不读取目标内容；目标按原始字节无损编码，不经过带替换的文本解码，任何字节级改向（含非 UTF-8 目标）都会改变 subject。payload 顶层含显式 `subject_version`（当前 `2`），参与摘要；升级后旧 ACCEPT 因缺少版本字段而一律 `REVIEW_STALE`。路径名称（文件名、链接名、目录名）按 JSON 转义无损编码：非 UTF-8 名称经 surrogateescape 得到的字符以 `\uXXXX` 写出，canonical JSON 为纯 ASCII，不再抛 `UnicodeEncodeError`；转义可逆且单射，不使用 `surrogatepass` 生成非法 UTF-8。scope 路径的每一级部件（祖先和末级）不得是符号链接，任一级为符号链接返回 `REVIEW_SUBJECT_INCOMPLETE`。新增、删除或改向链接都会改变 subject；排除树内的链接跳过、不记录；fifo/socket/设备等非 regular/dir/symlink 的特殊文件返回 `REVIEW_SUBJECT_INCOMPLETE`。排除 `.git`/`.venv`/`.venv-mcp`/`.scs`/`.archify` 和 `<docs_root>/agent/evidence`、`<docs_root>/agent/acceptance`、`<docs_root>/agent/view-archives`；上限 500 条目/100 MB（链接也计入条目上限），超限 `REVIEW_SUBJECT_TOO_LARGE`；读取失败/不可读 `REVIEW_SUBJECT_INCOMPLETE`。快照后文件或链接变化会在下一次重算表现为 `REVIEW_STALE`。

### 证据与资源

证据路径 `<docs-root>/agent/evidence/WP-.../R1/T1/<attempt>/result.json`。`evidence_verify(session_id,package_id,revision,subtask_id,path,required_content=[])` 返回 VERIFIED/INVALID/INSUFFICIENT。UTF-8、非空、普通文件、最大 2 MB；测试证据 JSON 必须有非空 command、整数 exit_code=0、正整数 passed；review 证据按上述 manifest 校验。在原生审阅模式下（或匹配到的 review attempt 锁定了 `review_route`），独立 `evidence_verify` 也会从证据路径提取 `attempt_id`，核验该 attempt 对应的 `review_subject`、`review_route` 和已登记的 `host_agent_id`（路径在 review 证据前缀下但缺少匹配 attempt 时返回 `INVALID` / `REVIEW_ATTEMPT_REQUIRED`）；它校验的是该 attempt 准备时冻结的快照 `review_subject`，最终 fresh 判定仍以 review attempt 提交及 `REVIEW_READY`/`COMPLETED`/`handoff_export` 重算时的 `REVIEW_STALE` 为准。不能证明命令真实执行；主对话核原始日志和环境。PASS 更新引用 `{path,sha256,required_content?}`，更新时再次验证，内容变动则拒绝。未测写 NOT_RUN；不把测试方法当通过。

`resource_acquire(session_id,resource="file:src/parser.py",ttl_seconds=120)`；资源类型 file/device/serial/programmer。3.0 该工具仍登记写租约；不提供共享读租约。续租携带 lease_token，释放也须 token；过期新 token 防止旧持有人释放新租约。同资源竞争返回 CONFLICT。锁为建议性，不阻止外部进程；目录范围锁不自动锁子文件，应采用同一具体文件/设备键，调用者统一资源命名。

`handoff_export(session_id,package_id,expected_version)` 返回本地审计 handoff_id 与 sha256；新对话 session_open 后 `handoff_resume(session_id,handoff_id,sha256)` 显式附加。只支持同 canonical 项目和同本地 DB；工作包已变更则 HANDOFF_STALE；review 已登记但 subject 变化时 export 返回 REVIEW_STALE。新对话必须自行确认 intake 才能修改工作包，不复制个人选择。handoff 不用于授权危险操作。

只读 Resources：`scs://session/{session_id}`、`scs://work-package/{package_id}`。本地服务无用户认证，知道 ID 的同一项目客户端可读取这些资源；不适用于不互信用户。

## 运行地图

`run_map(session_id, package_id?=None, revision?=None, limit=100)` 只读单事务，返回 `packages`/`subtasks`/`attempts`/`notes`/`blockers`/`review`、`mermaid`、`captured_at`、`truncated`、`limits`；不写文件、不刷新视图、不重算 review subject。

- 上限：包 ≤64（`limit` 可更小）、attempt ≤200、subtask 展示 ≤500、每 attempt 最多展示 3 条 note；包/attempt/subtask 超限时标 `truncated`。
- `revision` 必须配合 `package_id`，读取当前修订或已存修订快照；无快照返回 `REVISION_NOT_FOUND`。
- `blockers` 包含 FAIL/BLOCKED 子任务、活动 attempt、`REVIEW_REQUIRED`/`REVIEW_REJECTED`；`review` 为包级复核摘要。
- Mermaid 节点 id 清洗为安全字符，标签转义引号/方括号/花括号/尖括号/井号/换行等；只用于展示。

## 破坏性操作闸门

闸门适用于所有模式。标准/快速碰到须用原生预审的破坏性操作先阻塞；只有用户明确切换到长任务后才启动长期模式的确认和原生预审，不自动升级、不补短模式复核题、不绕过。只调查不执行。MCP 只登记决定并回答执行前查询，不执行、不拦截宿主命令；没有宿主 Hook 时是协议级约束（`protocol_only=true`、`execution_intercepted=false`）。

```text
operation_gate_record(
  session_id, operation_id, command, cwd, targets, impact, backup, rollback,
  verdict: ACCEPT | REJECT | UNAVAILABLE_OVERRIDE,
  user_confirmed: bool,
  report_path=None, reason=None, baseline=None, review_receipt=None
)
```

- `cwd` 必须是项目根内已存在目录；`targets` 1..100，项目相对路径或 `device:`/`serial:`/`programmer:` 逻辑 ID；`baseline` 为 `{"head": "<7..64 hex>"}` 或 `{"files": {path: sha256}}`（≤200 项）。先用新 ID 和完整待审批参数调用 `operation_gate_check`；`MISSING` 响应中的 `operation_digest` 用于预审，不代表批准。
- ACCEPT/REJECT 必须提交原生 `review_receipt`。字段同包级 v2 manifest，`agent_type` 须匹配确认路由对应的审阅员派发 ID；`operation_digest` 替换 `review_subject`，且 `final_message.path` 为**项目相对路径**；可选 `report_path` 是同一路径的别名，如同时传必须一致。主对话保存真实最终消息（UTF-8、≤2 MB），其唯一结论须与 verdict 一致。ACCEPT 还需要用户独立确认 `user_confirmed=true`。
- REJECT 对同 `operation_id`+digest 是终态：同 verdict 幂等，后续 ACCEPT 返回 `GATE_REJECTED`；不同 digest 返回 `OPERATION_CONFLICT`。
- `UNAVAILABLE_OVERRIDE` 仅在原生复核不可用时据实记录：`user_confirmed=true` 且 `reason` ≥10 字；可附失败/残缺报告；不是 APPROVED。之后只有同 digest 的真实合格复核及独立确认才可记 ACCEPT。
- 事件只存 ≤16 KB 摘要，不存报告全文。

```text
operation_gate_check(
  session_id, operation_id, command=None, cwd=None, targets=None, baseline=None
)
```

返回 `status=MISSING|APPROVED|REJECTED|UNAVAILABLE_OVERRIDE|STALE`，以及 `approved`、`reasons`、`expected_digest`/`computed_digest`（STALE 时）和记录摘要。只读、不写事件；`APPROVED` 仍带 `user_confirmation_still_required=true`，不是执行授权。`check` 不重读报告文件，报告以登记时 sha256 固化。在原生审阅模式下，重开 `mode`（使模式待确认）、从 `long` 切出到非 `long` 模式，或重开/变更 `review_route` 都会使当前会话的有效复核路由失效或不匹配，既有破坏性操作批准在 `operation_gate_check` 中立即变为 `STALE`（`reasons=["REVIEW_ROUTE_MISMATCH"]`），必须重新确认长任务与复核路由后重新预审与登记。

示例：

```json
{
  "session_id": "S-...", "operation_id": "clean-build-1",
  "command": "rm -rf build", "cwd": "/abs/project", "targets": ["build"],
  "impact": "删除构建产物", "backup": "build 可由源码重建", "rollback": "重新构建",
  "verdict": "ACCEPT", "user_confirmed": true,
  "review_receipt": {
    "schema_version": 2,
    "operation_digest": "<检查返回的候选 digest>",
    "agent_type": "继承审阅员", "agent_id": "<实际完成 ID>",
    "model": "unobserved", "status": "completed", "verdict": "ACCEPT",
    "final_message": {"path": "docs/agent/evidence/gate-clean-build.md", "sha256": "<原始最终消息 SHA-256>"}
  }
}
```

执行前再调用：

```json
{"session_id":"S-...","operation_id":"clean-build-1","command":"rm -rf build","cwd":"/abs/project","targets":["build"]}
```

## 文件同步与回退

SQLite 是结构化状态源；AGENT_PROJECT.md、AGENT_COORDINATION.md 是主对话维护的可审查记忆，evidence 是原始证据，04_test/report/ 是审查报告。服务仅创建缺失记忆文件，所有变更返回 record_sync_required；主对话将修订、结果、证据索引和 handoff 追加到既有记录，发现用户编辑冲突时停止并核对。

MCP 断开时，日常任务仍按权限使用宿主原生 Agent；长任务用原生卡片或文本合并确认，并手工维护任务、修订、证据和真实派发 ID。正式审阅和操作闸门仍要求可核验的结果；没有当前 `APPROVED` 时不得执行破坏性操作。恢复时核对 `session_get`、`work_package_get` 与手工记录，再显式选择如何录入。项目迁移后按 canonical 根恢复可审查的定义和证据，旧会话确认不复制。

## 错误码速查

| 错误码 | 场景 |
| --- | --- |
| `INTAKE_REQUIRED` | 未满足适用验证/复核时机，或启用原生复核但未确认路由；返回 `reason/source/policy` |
| `REVIEW_REQUIRED` | `review_required=true` 但没有当前 PASS ACCEPT review（含未声明 review 子任务） |
| `REVIEW_STALE` | review subject 已变化；manifest subject 不匹配；COMPLETED/export 时重算不一致 |
| `REVIEW_WINDOW_ACTIVE` | review attempt 与其他活动 attempt 同时存在，或批量中 review 未单独运行 |
| `REVIEW_ATTEMPT_REQUIRED` | 用 `work_package_update(results=...)` 直接写 review 子任务，或原生审阅模式下独立 `evidence_verify` 的 review 证据路径缺少匹配 attempt |
| `REVIEW_ROUTE_REQUIRED` | 原生审阅下未确认 `review_route`（`gpt`/`inherit`），或当前模式非 `long` 无法确认复核路由 |
| `NATIVE_REVIEW_DISABLED` | 未启用 `SCS_NATIVE_REVIEW=1`，却访问 `review_route`、原生 review 工作包或已用原生回执的操作闸门 |
| `REVIEW_AGENT_MISMATCH` | 原生 review manifest/回执的 `agent_type` 与确认路由不符、`agent_id` 与已登记 `host_agent_id` 不一致，或尝试替换原生 review attempt 已记录的 `host_agent_id` |
| `REVIEW_FINAL_MESSAGE_NOT_TEXT` | 复核原始最终消息不是合法 UTF-8 文本 |
| `HOST_AGENT_ACTIVE` | 原生审阅下同一 `host_agent_id` 在全局已有其他非 `ENDED` 的活动 attempt，禁止并发复用 |
| `HOST_AGENT_MISMATCH` | 原生审阅下尝试将已绑定 `host_agent_id` 的活动非 review attempt 换绑为另一个 Agent ID |
| `REVIEW_SUBJECT_TOO_LARGE` | review_subject 文件快照超过 500 文件/100 MB |
| `REVIEW_SUBJECT_INCOMPLETE` | 快照读取中文件变化/不可读 |
| `REVIEW_INVALID` / `REVIEW_VERDICT_INVALID` | review manifest 数量/结论与结果状态不符 |
| `REVIEW_MANIFEST_REQUIRED` / `REVIEW_MANIFEST_INVALID` | review 证据不是严格 JSON 或字段集不符 |
| `REVIEW_RAW_SCOPE` / `REVIEW_FINAL_MESSAGE_HASH_MISMATCH` | `final_message.path` 越出 attempt 证据目录 / 原始消息 sha256 不符 |
| `REVIEW_CONCLUSION_MISSING_OR_AMBIGUOUS` / `REVIEW_VERDICT_CONFLICT` | 原始最终消息结论行不唯一/与 verdict 冲突 |
| `RESOURCE_READ_UNSUPPORTED` | 显式声明 `file:` + `read`；3.0 无共享文件读租约 |
| `BATCH_PACKAGE_MISMATCH` / `BATCH_REVISION_MISMATCH` | 批量 attempt 跨包/跨修订 |
| `ATTEMPTS_ACTIVE` / `ATTEMPT_ACTIVE` | 静止屏障/子任务已有活动 attempt |
| `GATE_INVALID` | gate 输入/报告/确认不合法 |
| `GATE_REJECTED` | 同 operation_id+digest 已有 REJECT，后续写 ACCEPT/override |
| `OPERATION_CONFLICT` | 同 operation_id 不同 digest/载荷，或 gate 已有 ACCEPT 再写 override |
| `VERSION_CONFLICT` | expected_version 过期；重读后再试 |
| `PACKAGE_CLOSED` / `ATTEMPT_CLOSED` | 终态包/已结束 attempt 不可再改 |
| `DEPENDENCY_NOT_READY` / `REVISION_CONFLICT` | 依赖未 PASS 或无活动 attempt；attempt 属于旧修订 |
| `EVIDENCE_REQUIRED` / `EVIDENCE_INVALID` / `EVIDENCE_CHANGED` | PASS 无证据/证据不合法或改动 |
| `INPUT_INVALID` / `SUBTASK_INVALID` | 参数、ID、子任务定义不合法 |

## Obsidian 项目视图

新增 project_view_refresh / project_view_info；当前仍是单个 MCP 服务、39 个工具（28 个协作/状态工具 + 11 个本机看板工具）。`interaction_ask` 提供 legacy form 的服务端超时。生成、自动刷新、首次打开、批注保留与升级边界见 [obsidian.md](obsidian.md)。`run_map` 不写视图文件；需要查看运行地图时直接读工具返回的 JSON/Mermaid。
