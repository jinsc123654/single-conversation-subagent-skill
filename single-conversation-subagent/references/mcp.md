# MCP 用法与恢复

## ZCode 固定审阅员回执

`SCS_NATIVE_REVIEW=1` 启用原生回执；用 `session_open.native_review` 核对。原有 `codex_gate=none|closure|packages`、`codex_review=no|yes` 和所有并行 attempt 接口保留。ZCode 把 `review_registration` 用于当前宿主有没有 `固定审阅员`。正式审查统一使用这个注册身份，策略确认时不另问审阅员路线。

- `work_attempt_prepare` 或 `work_attempt_prepare_many` 在原生模式返回 `agent_type`。执行角色按依赖、范围、资源派发。复核在其它 attempt 结束后单独 prepare；记录宿主真实返回的 `host_agent_id`。结果达到 `ENDED` 时提交其证据，按 `review_subject` 绑定 prepare 时冻结的对象。
- 在返回的 `evidence_dir` 中保存 `final-message.md` 和 `manifest.json`。最终消息来自实际宿主结果，恰好一行 `结论: ACCEPT` 或 `结论: REJECT`。`manifest.json` 使用以下字段；`model` 用宿主观测到的 `provider/model` 或 `unobserved`。一个合法的 REJECT 对应 `FAIL`。

```json
{
  "schema_version": 2,
  "review_subject": "<prepare 返回的 64 位十六进制哈希>",
  "agent_type": "固定审阅员",
  "agent_id": "<ZCode 真实 Agent ID>",
  "model": "unobserved",
  "status": "completed",
  "verdict": "ACCEPT",
  "final_message": {"path": "final-message.md", "sha256": "<原文 sha256>"}
}
```

- 操作预审先用精确命令和范围调用 `operation_gate_check` 获取 `operation_digest`。同一回执结构用 `operation_digest` 替换 `review_subject`，`final_message.path` 用项目相对路径。将它作为 `operation_gate_record.review_receipt` 提交。只有 `APPROVED` 且有独立用户确认才执行。主对话核对真实角色、实际完成和原件，MCP 校验结构、身份绑定和哈希。
- 收尾审查由主对话按已选策略单独派发，原样留存宿主回执和最终消息。已经存档的 Codex 回执可查看；当前 native `packages` 放行和预审授权要求固定审阅员回执。下面的 Codex JSON 和命令说明供通用宿主使用。


纯 Skill 无 Python/MCP 依赖。混合模式只在工具可见时使用；可见不代表模型一定调用，也不证明 UI 显示。服务无原生 Agent 调度工具。

启动：`python -m scs_mcp --project-root /absolute/project`。仅 stdio；项目根是启动时明确允许的边界，工具不能换根。状态为该根下 `.scs/state.sqlite3`，SQLite WAL，schema 3，busy timeout 3 秒。3.0 不新增 schema：`codex_gate` 存 `questions` 行，破坏性闸门记录存 `events`，旧库直接可用。只支持本地单用户项目；不提供认证或跨用户服务。

## 初次交互

对话给出项目目录时用该目录，否则用宿主提供的当前对话工作目录。传绝对路径并设置 `register_obsidian=true` 时，`session_open` 会在提问前初始化 docs-root 和总览，并登记给 PyQt。成功时返回 `obsidian.status=REGISTERED` 和绝对路径。登记失败时在 `obsidian` 返回错误，已打开的会话可继续使用。只有在项目元数据及本地索引写入获准时启用登记。

1. `session_open(client_name=..., conversation_id="宿主真实对话ID", review_registration="registered|unregistered|unknown", project_root="任务项目根")`。先完成一次任务相关只读调查（目标目录/README/git status；不要只列目录就提问），不要检查 MCP 注册/配置/项目根，不读无关记忆；调查在绑定前完成，`session_open` 只调用一次。`project_root` 传任务项目根；服务端启动配置的根是允许的工作区边界，项目根必须是其子目录，服务端为该项目建独立 `<项目>/.scs`；省略时用配置根。`client_name` 可省略，服务端把配置 host 与 client_name 一起做结构化 SHA-256 命名空间（存储 `h<sha256>`，返回 `client_label` 供阅读），不同宿主同 ID 不共用会话。返回 `question_channel`（auto/native/mcp/text）、`host`、`project_root`、`codex_gate`（未确认为 null）和 `intake` 投影。仅未开始模式选择时附短 `workspace_scan` 和 `mode_options`。ID 未知时省略并保留返回 ID；不得伪造真实 ID、用项目路径当对话 ID。根据当前宿主会话的技能列表填写 review_registration，有 codex-review 时传 registered，明确不可用时传 unregistered，无法核实时传 unknown。
2. 按 `question_channel` 路由。`native`：用宿主原生 Ask/Question，没有时用文本；`text`：完整显示选项、推荐与 question_id，结束当轮等待；`mcp`：`interaction_ask(session_id=...,kind="mode",recommendation=["standard"],timeout_seconds=15,analysis="推荐 standard：范围小，先冒烟")`，仅 legacy stdio 的 back-channel 打开 MCP form，到点标记 `TIMED_OUT` 并返回 recommendation，不写 `CONFIRMED`；`auto`：form 可用则按 mcp，否则按 native/text。`analysis` 是 1-2 句推荐理由（≤600 字，不含隐私/凭据）；form 的选项值带中文注释（如 `quick — 快速处理`），并显示中文 title、分析、推荐标记、15s 截止和 question_id。
3. `MODERN_NO_BACKCHANNEL`/`ELICITATION_UNSUPPORTED`/`TEXT_FALLBACK_REQUIRED`：问题保持 `WAITING_USER` 并带 `question_id`；显示完整选项与唯一推荐，用户明确回复后直接 `interaction_submit(...,action="accept",choices=[...],source="text"|"native")`。modern 无回通道只表示不能做服务端限时 form，不等于客户端完全不支持 elicitation。原生/文本路径不声称 15 秒；15 秒只用于 mcp form 路径。ZCode 原生 AskUserQuestion 是 60 秒隐藏 + 5 分钟截止。
4. 需要显式 MCP 记账时，`interaction_request(...,channel="native")` 只登记，然后真实调用原生工具，再将用户明确答复以返回 `question_id` 提交；服务不越过宿主调用 UI。
5. TEXT_FALLBACK_REQUIRED：显示 display_text 中完整选项与唯一推荐，结束当轮等回复；再 `interaction_submit(session_id=...,question_id=...,action="accept",choices=["long"],source="text")`。不把文本当原生弹窗成功。
6. `session_get` 读取已确认选择、`codex_gate`、`intake` 与未决问题。同题重复 request 不弹第二个 UI，已确认直接返回 ALREADY_CONFIRMED。同一对话沿用已确认的 `mode`/`verification`/`codex_gate`/`codex_review`，不重复提问；只有用户显式说“使用长任务/只调查/快速处理/标准执行”才 `interaction_reopen(kind="mode", explicit_user_change=true)`，随后 `interaction_request(channel="text", recommendation=[新模式])` → `interaction_submit(accept)`，不弹表单。`mcp` 15s 超时后模型继续，表单仍由宿主持有，提示用户稍后忽略，不重复提交。

模式值 `quick/standard/long/investigate`。推荐是单元素列表。仅首次进入 standard/long 后请求 verification，值为 `smoke/targeted/full/stability/none/method-only`，支持多选；推荐只有一个列表组合。`none` 和 `method-only` 各自排除所有其他项。冒烟本轮，定向/全量/稳定性收尾；用户指定预算、资源、方法写入协调记录。危险操作不在此表单授权范围。

## 问题顺序、codex_gate 与 intake

顺序固定：`mode` → `verification`（仅 standard/long）→ 后续题。长任务只问 `codex_gate`；标准/快速保留 `codex_review`（仅注册已确认时）。

| 模式 | 验证范围 | 后续复核题 |
| --- | --- | --- |
| quick | 不问 | `codex_review` yes/no（registered 时）；unknown/unregistered 跳过 |
| standard | 必问 | `codex_review` yes/no（registered 时）；未知/未注册跳过 |
| long | 必问 | `codex_gate` none/closure/packages；不再问 `codex_review` |
| investigate | 不问 | 跳过 |

`codex_gate` 值：

| 值 | 含义 | MCP 行为 |
| --- | --- | --- |
| `none` | 不做 Codex 复核或收尾审查 | 不设置 `review_required` |
| `closure` | 仅收尾一次 Codex 只读审查 | 不设置 `review_required`；收尾由 Skill 执行 |
| `packages` | 每个工作包复核 + 收尾审查（推荐） | 新包 `review_required=true`；`REVIEW_READY`/`COMPLETED` 需当前 subject 的 PASS ACCEPT review |

- 推荐 `["packages"]` 只是建议，必须用户明确提交；不会自动写入。
- `packages` 依赖 Codex 可用；codex-review 未注册时 Skill 应说明并推荐 `none`/`closure`，但 MCP 仍接受三种值。
- long 的 `codex_review` 返回 `SKIPPED/USE_CODEX_GATE`；其他模式的 `codex_review` 保持 yes/no 原语义。
- 旧长会话没有 gate（验证范围已确认）：`session_get.codex_gate=null`，`intake.ready=false`、`reason=CODEX_GATE_REQUIRED`；创建/更新或手动 `project_view_refresh` 返回 `INTAKE_REQUIRED`，补问一次即可。不迁移旧 `codex_review`，也不自动写 `packages`。

`intake` 是共享投影：

```json
{"ready": false, "policy": null, "source": "codex_gate", "reason": "CODEX_GATE_REQUIRED"}
```

- long：需当前 `CONFIRMED` gate；`policy` 为 gate 值。
- standard：需 verification；`registered` 时还需 `codex_review` 非 null；ready 后 `policy` 由 `codex_review` 映射（yes→closure，no→none）。
- quick/investigate（及未知旧 mode）：无额外要求。
- 推荐理由一句话，最多 160 字；首次用 `workspace_scan` 辅助判断范围，不把扫描全文回传到每道题。

ZCode 长任务必须先取得一次 `方案顾问` 的真实回执，再拆包派发。主对话将结果写入项目协作记录；`codex_review` 和 `codex_gate` 记录的都是正式审查策略。其他宿主的方案指导保留原行为。

## 恢复会话与补登记

传 `session_open(project_root=<原项目绝对路径>, resume_session_id=<原session_id>, review_registration="registered")`，沿用首次调用的 client_name（原来省略则仍省略）。省略 conversation_id 和 explicit_skip。只在当前宿主确认审查可用时补登记为 registered；unknown 保留已有确定状态。服务恢复同一会话 ID 和选项；项目或宿主不匹配返回 SESSION_NOT_FOUND。此路径也用于 MCP 进程重启后的临时会话重连。恢复不会复制 gate 答案到新对话，也不改变已确认的 gate。

## 问题与故障

逐题等待 `interaction_submit` 返回 `CONFIRMED` 再请求下一题。`QUESTION_PENDING` 返回原题号、类型和状态；使用真实答复提交原题，等待中保持同一题。开场记录复核选择，执行及已选验证完成后才实际运行审查。

`interaction_submit` 接受 accept/decline/cancel/timeout/unsupported/error。accept 必须有明确合法选择。timeout 需要宿主可观测超时回执 `observed_timeout=true`，仍不自动选项；`interaction_ask` 的服务端截止时间即该回执，超时返回推荐项供主对话继续，不写 `CONFIRMED`。RPC 超时提交 error，不能填 timeout。decline/cancel/timeout 保持终态未确认。

重试或用户明确改变时用 `interaction_reopen(session_id,kind,explicit_user_change=...)`；活动/已确认问题必须有用户明确变更。然后 request 获取新 question_id，旧回复返回 LATE_REPLY。RPC 异常会把问题标为 `FAILED`，需显式 reopen 后重试；无 form/modern 回落不标 terminal，保持 `WAITING_USER`，直接按同一 question_id 提交文本/原生答复。客户端重启使 SDK 密封 requestState 失效时，核对 session_get 的未决题，显式 reopen，再请求；不要复用旧响应。

服务可能观察 REQUESTED/WAITING_USER，不能证明已渲染；统一 UI_NOT_OBSERVABLE，真实接受回执只证明客户端提交了内容。模型可能未调用工具，客户端也可能自动生成答案；最终应以宿主实际用户交互记录核对。

`diagnostics_check(session_id=...)`：版本、DB quick_check、工具注册（当前 38 个）、协议、声明能力、最近 30 条脱敏事件。SKILL_NOT_LOADED 需宿主加载证据；TOOL_NOT_CALLED 需宿主调用日志。RPC_ERROR 与 ELICITATION_UNSUPPORTED 可在服务观察；用户取消/拒绝/接受分别 USER_CANCELLED/USER_DECLINED/USER_CONFIRMED。stdout 仅协议；默认错误日志 stderr，不启用 SDK 调试日志以免写入调用输入。

## 工作包、证据与租约

小问题无需工作包。标准示例：

```json
{"session_id":"S-...","goal":"修复解析器","allowed_files":["src/parser.py"],"acceptance":["空输入不异常"],"request_key":"parser-1","subtasks":[{"id":"T1","kind":"test","allowed_files":["src/parser.py"],"acceptance":["定向用例通过"]}]}
```

`packages` 长任务包应声明一个 review 子任务：

```json
{"session_id":"S-...","goal":"修复解析器","allowed_files":["src/parser.py"],"acceptance":["空输入不异常","复核 ACCEPT"],"request_key":"parser-r1","subtasks":[
  {"id":"T1","kind":"implement","allowed_files":["src/parser.py"],"acceptance":["空输入不异常"]},
  {"id":"R1","kind":"review","allowed_files":["src/parser.py"],"acceptance":["Codex 只读复核 ACCEPT"],"depends_on":["T1"]}
]}
```

传给 work_package_create，返回唯一 WP ID、revision=1、version=1、docs_root、`review_required` 和待同步记录路径。request_key 在同对话内幂等；不同定义同键冲突。subtask kind 为 analyze/implement/test/review；review 记录主对话启动的 Codex 只读复核，不是宿主子 Agent。文件范围为明确相对文件/目录，不是 glob；子任务不可扩大父范围。服务不授予文件或执行权限。

`work_package_update` 需 expected_version，成功加一，旧版本 VERSION_CONFLICT；不要盲重试。状态 PENDING → IN_PROGRESS → REVIEW_READY，BLOCKED 可恢复为 IN_PROGRESS；验收通过用 COMPLETED、重复/被取代用 ABANDONED 关闭，终态必须带 close_reason 且不可修改。`packages` 包在 REVIEW_READY/COMPLETED 需要 review，`REVIEW_REQUIRED`/`REVIEW_STALE` 分别表示缺少和过期；`work_package_update(results=...)` 直接写 review 子任务返回 `REVIEW_ATTEMPT_REQUIRED`。桌面管理器关闭滞留/重复包时调用 `work_package_close(project_root, package_id, expected_version, state, close_reason)`；它是本地操作员路径，仍从包内读取 owner session 并写审计，主对话仍应优先用 `work_package_update`。`force=true` 是操作员例外，`force_closed` 不表示 review-accepted。

### 并发 attempt

- `work_attempt_prepare(session_id, package_id, expected_version, subtask_id, host_agent_id?=None, ttl_seconds=120)`：校验依赖已 `PASS`/`SUBSTITUTED` 且无活动 attempt，原子占用子任务声明的完整资源集合，建立 `.../R<rev>/<subtask>/<attempt>/` 证据目录；返回 `attempt`、`leases`、`evidence_dir`、`warnings`。成功后才调用宿主 Agent。工作包 version 不变。
- `work_attempt_prepare_many(session_id, package_id, expected_version, subtask_ids, host_agent_ids?=None, ttl_seconds=120)`：单包单事务批量 prepare，全有或全无；成功后 version 不变；返回 `attempts[]`。重复 ID 返回 INPUT_INVALID，缺失子任务 SUBTASK_INVALID；同批依赖按波次；含 review 且多于一项返回 REVIEW_WINDOW_ACTIVE。`host_agent_ids` 可选，提供时与 `subtask_ids` 一一对应。
- `work_attempt_update(session_id, attempt_id, expected_version, state?, result?, host_agent_id?, operation_id?)`：`PREPARED → LAUNCHING → RUNNING → DRAINING → ENDED`，另有 `UNKNOWN`；`ENDED` 写入子任务结果并让工作包 version+1，`PASS` 必须有 attempt 目录内可验证证据；`UNKNOWN` 不释放资源、不自动重派。`operation_id` 同载荷重试返回原回执。
- `work_attempt_update_many(session_id, updates=[{attempt_id,result}], operation_id)`：只接受 ENDED 结果；同包当前修订；全有或全无；成功只 version+1；`operation_id` 必填且重放先于状态/版本检查；review 不能与其他项混批。跨包/跨修订返回 BATCH_PACKAGE_MISMATCH/BATCH_REVISION_MISMATCH。
- `work_attempt_list(session_id, package_id, revision?)`：只读查询 attempt 生命周期、宿主、结果和资源。
- `work_attempt_note(session_id, attempt_id, note, note_id)`：追加审计备注，note 1..2000 字、note_id 为简单标识；不写版本、不续租、不证明进程存活；同 note_id 同载荷幂等，不同载荷 OPERATION_CONFLICT。
- 子任务定义支持 `depends_on`、`resources`（`file:`/`device:`/`serial:`/`programmer:` + `read`/`write`；3.0 文件只支持 `write`）和 `required`。目录资源覆盖后代。
- **读租约限制（3.0）**：只有 implement 默认对每个 scope 取独占写租约；analyze/test/review 默认 `[]`。显式 `file:` + `read` 返回 `RESOURCE_READ_UNSUPPORTED`；设备类 read 未禁用。旧包已存的文件 read 资源在 prepare 时过滤并返回 `warnings[]`（`READ_LEASE_SKIPPED`），不阻断。本版没有共享文件读保护。
- review attempt 必须单独运行：prepare 时包内不得有其他活动 attempt；review 活动期间不得 prepare 其他子任务；批量中 review 必须单独一批，否则 REVIEW_WINDOW_ACTIVE。
- `REVIEW_READY`/`COMPLETED`/`ABANDONED`/`new_revision`/`handoff_export` 前必须有静止屏障；有活动 attempt 时 `work_package_update` 不接受直接子任务结果。结果 PASS/FAIL/BLOCKED/NOT_RUN/SUBSTITUTED；PASS 是子任务证据报告，不是最终接受。new_revision=true 单独调用，清空结果并分配新证据目录，不删除旧文件；`review_required` 沿用。

### review 子任务与证据

1. 非 review 子任务完成且包内无活动 attempt；`work_attempt_prepare` 单独准备 review attempt，返回 `review_subject`。
2. 主对话启动 Codex 只读复核，把 manifest 和原始最终消息写入该 attempt 的证据目录。
3. `work_attempt_update(state="ENDED", result={"status":"PASS","evidence":[...manifest...]})` 记录 ACCEPT；REJECT 用 `FAIL` + manifest。只有 ACCEPT 映射 PASS。
4. `REVIEW_READY`/`COMPLETED` 检查 PASS review 的 verdict=ACCEPT 且 subject 当前；`COMPLETED` 和 `handoff_export` 重算 subject，不一致 `REVIEW_STALE`。`packages` 包未声明 review 子任务时 REVIEW_READY 返回 `REVIEW_REQUIRED`。

review manifest 是严格 JSON 对象，字段集恰好：

```json
{
  "verdict": "ACCEPT",
  "session_id": "codex-session-id",
  "model": "gpt-6.2-sol",
  "subject_sha256": "<prepare 返回的 review_subject>",
  "final_message_sha256": "<raw final message 的 sha256>",
  "command": "codex exec ...",
  "exit_code": 0,
  "raw_path": "final-message.md"
}
```

- `verdict` 只能是 ACCEPT/REJECT，是唯一判据；`required_content` 在 review 分支被忽略。
- `exit_code` 必须是 int 0；作为 review attempt 证据提交时 `subject_sha256` 不匹配返回 `REVIEW_STALE`。
- `raw_path` 相对 manifest 目录且解析后仍在本次 attempt 证据目录；不得绝对路径、反斜杠或 `..`。
- 原始最终消息必须存在、sha256 匹配、UTF-8，且恰好一行 `结论: ACCEPT` 或 `结论: REJECT`（允许 `**...**` 和中文冒号），与 verdict 一致。
- ACCEPT/REJECT 都是合法报告；REJECT 以 FAIL 落账，整改后用新 review attempt。Codex 不可用/超时/输出残缺时不能 PASS，保持 REVIEW_REQUIRED，不得伪造 ACCEPT。

`review_subject` 覆盖修订定义（goal/allowed_files/acceptance/非 review 子任务，按 id 排序）、非 review 子任务的 status+evidence(path,sha256)、包 allowed_files 的有界快照：regular 文件 path→sha256；范围内每个符号链接记录相对路径→`readlink` 目标的原始字节的十六进制（有链接时才出现 `links` 字段），不跟随、不读取目标内容；目标按原始字节无损编码，不经过带替换的文本解码，任何字节级改向（含非 UTF-8 目标）都会改变 subject。payload 顶层含显式 `subject_version`（当前 `2`），参与摘要；升级后旧 ACCEPT 因缺少版本字段而一律 `REVIEW_STALE`。路径名称（文件名、链接名、目录名）按 JSON 转义无损编码：非 UTF-8 名称经 surrogateescape 得到的字符以 `\uXXXX` 写出，canonical JSON 为纯 ASCII，不再抛 `UnicodeEncodeError`；转义可逆且单射，不使用 `surrogatepass` 生成非法 UTF-8。scope 路径的每一级部件（祖先和末级）不得是符号链接，任一级为符号链接返回 `REVIEW_SUBJECT_INCOMPLETE`。新增、删除或改向链接都会改变 subject；排除树内的链接跳过、不记录；fifo/socket/设备等非 regular/dir/symlink 的特殊文件返回 `REVIEW_SUBJECT_INCOMPLETE`。排除 `.git`/`.venv`/`.venv-mcp`/`.scs`/`.archify` 和 `<docs_root>/agent/evidence`；上限 500 条目/100 MB（链接也计入条目上限），超限 `REVIEW_SUBJECT_TOO_LARGE`；读取失败/不可读 `REVIEW_SUBJECT_INCOMPLETE`。快照后文件或链接变化会在下一次重算表现为 `REVIEW_STALE`。

### 证据与资源

证据路径 `<docs-root>/agent/evidence/WP-.../R1/T1/<attempt>/result.json`。`evidence_verify(session_id,package_id,revision,subtask_id,path,required_content=[])` 返回 VERIFIED/INVALID/INSUFFICIENT。UTF-8、非空、普通文件、最大 2 MB；测试证据 JSON 必须有非空 command、整数 exit_code=0、正整数 passed；review 证据按上述 manifest 校验。独立 `evidence_verify` 不传 subject，只校验 manifest 结构/原始消息，不判定 subject 是否当前；最终 fresh 判定以 review attempt 提交时的 `REVIEW_STALE` 为准。不能证明命令真实执行；主对话核原始日志和环境。PASS 更新引用 `{path,sha256,required_content?}`，更新时再次验证，内容变动则拒绝。未测写 NOT_RUN；不把测试方法当通过。

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

MCP 只登记决定并回答执行前查询，不执行、不拦截宿主命令；没有宿主 Hook 时是协议级约束（`protocol_only=true`、`execution_intercepted=false`）。

```text
operation_gate_record(
  session_id, operation_id, command, cwd, targets, impact, backup, rollback,
  verdict: ACCEPT | REJECT | UNAVAILABLE_OVERRIDE,
  user_confirmed: bool,
  report_path=None, reason=None, baseline=None
)
```

- `cwd` 必须是项目根内已存在目录；`targets` 1..100，项目相对路径或 `device:`/`serial:`/`programmer:` 逻辑 ID；`baseline` 为 `{"head": "<7..64 hex>"}` 或 `{"files": {path: sha256}}`（≤200 项）。
- ACCEPT/REJECT 必须有项目内报告：UTF-8、≤2 MB，正文恰好一行 `结论: ACCEPT`/`结论: REJECT`（允许 `**...**`）；ACCEPT 还需 `user_confirmed=true`。
- REJECT 对同 `operation_id`+digest 是终态：同 verdict 幂等，后续 ACCEPT 返回 `GATE_REJECTED`；不同 digest 返回 `OPERATION_CONFLICT`。
- `UNAVAILABLE_OVERRIDE` 仅 Codex 不可用时使用：`user_confirmed=true` 且 `reason` ≥10 字；可附失败/残缺报告；永远不返回 APPROVED。之后可补记同 digest ACCEPT。
- 事件只存 ≤16 KB 摘要，不存报告全文；同 `operation_id` 不同 digest 冲突。

```text
operation_gate_check(
  session_id, operation_id, command=None, cwd=None, targets=None, baseline=None
)
```

返回 `status=MISSING|APPROVED|REJECTED|UNAVAILABLE_OVERRIDE|STALE`，以及 `approved`、`reasons`、`expected_digest`/`computed_digest`（STALE 时）和记录摘要。只读、不写事件；`APPROVED` 仍带 `user_confirmation_still_required=true`，不是执行授权。`check` 不重读报告文件，报告以登记时 sha256 固化。

示例：

```json
{"session_id":"S-...","operation_id":"clean-build-1","command":"rm -rf build","cwd":"/abs/project","targets":["build"],"impact":"删除构建产物","backup":"build 可由源码重建","rollback":"重新构建","verdict":"ACCEPT","user_confirmed":true,"report_path":"docs/agent/evidence/gate-clean-build.md"}
```

执行前再调用：

```json
{"session_id":"S-...","operation_id":"clean-build-1","command":"rm -rf build","cwd":"/abs/project","targets":["build"]}
```

## 文件同步与回退

SQLite 是结构化状态源；AGENT_PROJECT.md、AGENT_COORDINATION.md 是主对话维护的可审查记忆，evidence 是原始证据，04_test/report/ 是审查报告。服务仅创建缺失记忆文件，所有变更返回 record_sync_required；主对话将修订、结果、证据索引和 handoff 追加到既有记录，发现用户编辑冲突时停止并核对。

MCP 断开：纯 Skill 继续合法文本交互，工作包/修订/证据按原路径维护，状态如实记录。MCP 恢复：核对 session_get/work_package_get 与手工记录；显式决定采用文件或 DB，必要时创建新修订后逐项录入。没有自动导入器，不能覆盖用户改动。迁移/移动整个项目后 DB 会因 canonical 根不匹配拒绝，先备份，通过审核的工作包定义和证据手工恢复到新根，不复制旧会话确认。

## 错误码速查

| 错误码 | 场景 |
| --- | --- |
| `INTAKE_REQUIRED` | long 未确认 gate；standard 未确认 verification/registered codex_review；返回 `reason/source/policy` |
| `REVIEW_REQUIRED` | `review_required=true` 但没有当前 PASS ACCEPT review（含未声明 review 子任务） |
| `REVIEW_STALE` | review subject 已变化；manifest subject 不匹配；COMPLETED/export 时重算不一致 |
| `REVIEW_WINDOW_ACTIVE` | review attempt 与其他活动 attempt 同时存在，或批量中 review 未单独运行 |
| `REVIEW_ATTEMPT_REQUIRED` | 用 `work_package_update(results=...)` 直接写 review 子任务 |
| `REVIEW_SUBJECT_TOO_LARGE` | review_subject 文件快照超过 500 文件/100 MB |
| `REVIEW_SUBJECT_INCOMPLETE` | 快照读取中文件变化/不可读 |
| `REVIEW_INVALID` / `REVIEW_VERDICT_INVALID` | review manifest 数量/结论与结果状态不符 |
| `REVIEW_MANIFEST_REQUIRED` / `REVIEW_MANIFEST_INVALID` | review 证据不是严格 JSON 或字段集不符 |
| `REVIEW_EXIT_NONZERO_OR_UNKNOWN` | review manifest 的 exit_code 非 int 0 |
| `REVIEW_RAW_SCOPE` / `REVIEW_FINAL_MESSAGE_HASH_MISMATCH` | raw_path 越出 attempt 证据目录 / 原始消息 sha256 不符 |
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

新增 project_view_refresh / project_view_info；当前仍是单个 MCP 服务、38 个工具（27 个协作/状态工具 + 11 个本机看板工具）。`interaction_ask` 提供 legacy form 的服务端超时。生成、自动刷新、首次打开、批注保留与升级边界见 [obsidian.md](obsidian.md)。`run_map` 不写视图文件；需要查看运行地图时直接读工具返回的 JSON/Mermaid。
