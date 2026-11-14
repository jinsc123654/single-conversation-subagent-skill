# MCP 用法与恢复

纯 Skill 无 Python/MCP 依赖。混合模式只在工具可见时使用；可见不代表模型一定调用，也不证明 UI 显示。服务无原生 Agent 调度工具。

启动：`python -m scs_mcp --project-root /absolute/project`。仅 stdio；项目根是启动时明确允许的边界，工具不能换根。状态为该根下 `.scs/state.sqlite3`，SQLite WAL，schema 3，busy timeout 3 秒。只支持本地单用户项目；不提供认证或跨用户服务。

## 初次交互

对话给出项目目录时用该目录，否则用宿主提供的当前对话工作目录。传绝对路径并设置 `register_obsidian=true` 时，`session_open` 会在提问前初始化 docs-root 和总览，并登记给 PyQt。成功时返回 `obsidian.status=REGISTERED` 和绝对路径。登记失败时在 `obsidian` 返回错误，已打开的会话可继续使用。只有在项目元数据及本地索引写入获准时启用登记。

1. `session_open(client_name=..., conversation_id="宿主真实对话ID", review_registration="registered|unregistered|unknown", project_root="任务项目根")`。先完成一次任务相关只读调查（目标目录/README/git status；不要只列目录就提问），不要检查 MCP 注册/配置/项目根，不读无关记忆；调查在绑定前完成，`session_open` 只调用一次。`project_root` 传任务项目根；服务端启动配置的根是允许的工作区边界，项目根必须是其子目录，服务端为该项目建独立 `<项目>/.scs`；省略时用配置根。`client_name` 可省略，服务端把配置 host 与 client_name 一起做结构化 SHA-256 命名空间（存储 `h<sha256>`，返回 `client_label` 供阅读），不同宿主同 ID 不共用会话。返回 `question_channel`（auto/native/mcp/text）、`host` 与 `project_root`。仅未开始模式选择时附短 `workspace_scan` 和 `mode_options`。ID 未知时省略并保留返回 ID；不得伪造真实 ID、用项目路径当对话 ID。根据当前宿主会话的技能列表填写 review_registration，有 codex-review 时传 registered，明确不可用时传 unregistered，无法核实时传 unknown。
2. 按 `question_channel` 路由。`native`：用宿主原生 Ask/Question，没有时用文本；`text`：完整显示选项、推荐与 question_id，结束当轮等待；`mcp`：`interaction_ask(session_id=...,kind="mode",recommendation=["standard"],timeout_seconds=15,analysis="推荐 standard：范围小，先冒烟")`，仅 legacy stdio 的 back-channel 打开 MCP form，到点标记 `TIMED_OUT` 并返回 recommendation，不写 `CONFIRMED`；`auto`：form 可用则按 mcp，否则按 native/text。`analysis` 是 1-2 句推荐理由（≤600 字，不含隐私/凭据）；form 的选项值带中文注释（如 `quick — 快速处理`），并显示中文 title、分析、推荐标记、15s 截止和 question_id。
3. `MODERN_NO_BACKCHANNEL`/`ELICITATION_UNSUPPORTED`/`TEXT_FALLBACK_REQUIRED`：问题保持 `WAITING_USER` 并带 `question_id`；显示完整选项与唯一推荐，用户明确回复后直接 `interaction_submit(...,action="accept",choices=[...],source="text"|"native")`。modern 无回通道只表示不能做服务端限时 form，不等于客户端完全不支持 elicitation。原生/文本路径不声称 15 秒；15 秒只用于 mcp form 路径。ZCode 原生 AskUserQuestion 是 60 秒隐藏 + 5 分钟截止。
4. 需要显式 MCP 记账时，`interaction_request(...,channel="native")` 只登记，然后真实调用原生工具，再将用户明确答复以返回 `question_id` 提交；服务不越过宿主调用 UI。
5. TEXT_FALLBACK_REQUIRED：显示 display_text 中完整选项与唯一推荐，结束当轮等回复；再 `interaction_submit(session_id=...,question_id=...,action="accept",choices=["long"],source="text")`。不把文本当原生弹窗成功。
6. `session_get` 读取已确认选择与未决问题。同题重复 request 不弹第二个 UI，已确认直接返回 ALREADY_CONFIRMED。同一对话沿用已确认的 `mode`/`verification`/`codex_review`，不重复提问；后续验证/审查问题的 `recommendation` 用上次确认值，15 秒超时自动继续上次选项。只有用户显式说“使用长任务/只调查/快速处理/标准执行”才 `interaction_reopen(kind="mode", explicit_user_change=true)`，随后 `interaction_request(channel="text", recommendation=[新模式])` → `interaction_submit(accept)`，不弹表单。`mcp` 15s 超时后模型继续，表单仍由宿主持有，提示用户稍后忽略，不重复提交。

模式值 `quick/standard/long/investigate`。推荐是单元素列表。仅首次进入 standard/long 后请求 verification，值为 `smoke/targeted/full/stability/none/method-only`，支持多选；推荐只有一个列表组合。`none` 和 `method-only` 各自排除所有其他项。冒烟本轮，定向/全量/稳定性收尾；用户指定预算、资源、方法写入协调记录。危险操作不在此表单授权范围。`long` 模式下所有任务性查找、分析、实现和测试由执行子 Agent 完成，主对话只保留目标、决策、未决问题、下一步、证据路径和记录。

verification 仅标准/长任务请求；快速/只调查跳过。codex_review 在任务开始时、快速/标准/长任务、只有宿主确证注册 codex-review 时才请求，值 `no/yes`，唯一推荐 `["no"]`；只调查、未知或未注册返回 SKIPPED；未知的原因为 CODEX_REVIEW_REGISTRATION_UNKNOWN，明确未注册为 CODEX_REVIEW_NOT_REGISTERED；标准/长任务要求先确认 verification，快速不要求。任务开始只记录选择，收尾再实际执行，不重复问。`explicit_skip=true` 仅用于用户明确“不用问，直接做”，记录 quick；需要改变模式时显式 reopen，不把自动检测当用户跳过。

## 恢复会话与补登记

传 `session_open(project_root=<原项目绝对路径>, resume_session_id=<原session_id>, review_registration="registered")`，沿用首次调用的 client_name（原来省略则仍省略）。省略 conversation_id 和 explicit_skip。只在当前宿主确认审查可用时补登记为 registered；unknown 保留已有确定状态。服务恢复同一会话 ID 和选项；项目或宿主不匹配返回 SESSION_NOT_FOUND。此路径也用于 MCP 进程重启后的临时会话重连。

## 问题与故障

逐题等待 `interaction_submit` 返回 `CONFIRMED` 再请求下一题。`QUESTION_PENDING` 返回原题号、类型和状态；使用真实答复提交原题，等待中保持同一题。开场记录 Codex 审查选择，执行及已选验证完成后才实际运行审查。

`interaction_submit` 接受 accept/decline/cancel/timeout/unsupported/error。accept 必须有明确合法选择。timeout 需要宿主可观测超时回执 `observed_timeout=true`，仍不自动选项；`interaction_ask` 的服务端截止时间即该回执，超时返回推荐项供主对话继续，不写 `CONFIRMED`。RPC 超时提交 error，不能填 timeout。decline/cancel/timeout 保持终态未确认。

重试或用户明确改变时用 `interaction_reopen(session_id,kind,explicit_user_change=...)`；活动/已确认问题必须有用户明确变更。然后 request 获取新 question_id，旧回复返回 LATE_REPLY。RPC 异常会把问题标为 `FAILED`，需显式 reopen 后重试；无 form/modern 回落不标 terminal，保持 `WAITING_USER`，直接按同一 question_id 提交文本/原生答复。客户端重启使 SDK 密封 requestState 失效时，核对 session_get 的未决题，显式 reopen，再请求；不要复用旧响应。

服务可能观察 REQUESTED/WAITING_USER，不能证明已渲染；统一 UI_NOT_OBSERVABLE，真实接受回执只证明客户端提交了内容。模型可能未调用工具，客户端也可能自动生成答案；最终应以宿主实际用户交互记录核对。

`diagnostics_check(session_id=...)`：版本、DB quick_check、工具注册、协议、声明能力、最近 30 条脱敏事件。SKILL_NOT_LOADED 需宿主加载证据；TOOL_NOT_CALLED 需宿主调用日志。RPC_ERROR 与 ELICITATION_UNSUPPORTED 可在服务观察；用户取消/拒绝/接受分别 USER_CANCELLED/USER_DECLINED/USER_CONFIRMED。stdout 仅协议；默认错误日志 stderr，不启用 SDK 调试日志以免写入调用输入。

## 工作包、证据与租约

小问题无需工作包。创建示例：

```json
{"session_id":"S-...","goal":"修复解析器","allowed_files":["src/parser.py"],"acceptance":["空输入不异常"],"request_key":"parser-1","subtasks":[{"id":"T1","kind":"test","allowed_files":["src/parser.py"],"acceptance":["定向用例通过"]}]}
```

传给 work_package_create，返回唯一 WP ID、revision=1、version=1、docs_root 和待同步记录路径。request_key 在同对话内幂等；不同定义同键冲突。subtask kind 为 analyze/implement/test。文件范围为明确相对文件/目录，不是 glob；子任务不可扩大父范围。服务不授予文件或执行权限。

`work_package_update` 需 expected_version，成功加一，旧版本 VERSION_CONFLICT；不要盲重试。状态 PENDING → IN_PROGRESS → REVIEW_READY，BLOCKED 可恢复为 IN_PROGRESS；验收通过用 COMPLETED、重复/被取代用 ABANDONED 关闭，终态必须带 close_reason 且不可修改。桌面管理器关闭滞留/重复包时调用 `work_package_close(project_root, package_id, expected_version, state, close_reason)`；它是本地操作员路径，仍从包内读取 owner session 并写审计，主对话仍应优先用 `work_package_update`。

### 并发 attempt

- `work_attempt_prepare(session_id, package_id, expected_version, subtask_id, host_agent_id?=None, ttl_seconds=120)`：校验依赖已 `PASS`/`SUBSTITUTED` 且无活动 attempt，原子占用子任务声明的完整资源集合，建立 `.../R<rev>/<subtask>/<attempt>/` 证据目录；返回 `attempt`、`leases`、`evidence_dir`。成功后才调用宿主 Agent。
- `work_attempt_update(session_id, attempt_id, expected_version, state?, result?, host_agent_id?, operation_id?)`：`PREPARED → LAUNCHING → RUNNING → DRAINING → ENDED`，另有 `UNKNOWN`；`ENDED` 写入子任务结果并让工作包 version+1，`PASS` 必须有 attempt 目录内可验证证据；`UNKNOWN` 不释放资源、不自动重派。`operation_id` 同载荷重试返回原回执。
- `work_attempt_list(session_id, package_id, revision?)`：只读查询 attempt 生命周期、宿主、结果和资源。
- 子任务定义支持 `depends_on`、`resources`（`file:`/`device:`/`serial:`/`programmer:` + `read`/`write`）和 `required`。目录资源覆盖后代；R/R 可并行，R/W、W/W 冲突；资源集合原子获取或全部失败。
- `REVIEW_READY`/`COMPLETED`/`ABANDONED`/`new_revision`/`handoff_export` 前必须有静止屏障；有活动 attempt 时 `work_package_update` 不接受直接子任务结果。结果 PASS/FAIL/BLOCKED/NOT_RUN/SUBSTITUTED；PASS 是子任务证据报告，不是最终接受。new_revision=true 单独调用，清空结果并分配新证据目录，不删除旧文件。

证据路径 `<docs-root>/agent/evidence/WP-.../R1/T1/result.json`。`evidence_verify(session_id,package_id,revision,subtask_id,path,required_content=[])` 返回 VERIFIED/INVALID/INSUFFICIENT。UTF-8、非空、普通文件、最大 2 MB；测试证据 JSON 必须有非空 command、整数 exit_code=0、正整数 passed。不能证明命令真实执行；主对话核原始日志和环境。PASS 更新引用 `{path,sha256,required_content?}`，更新时再次验证，内容变动则拒绝。未测写 NOT_RUN；不把测试方法当通过。

`resource_acquire(session_id,resource="file:src/parser.py",ttl_seconds=120)`；资源类型 file/device/serial/programmer。续租携带 lease_token，释放也须 token；过期新 token 防止旧持有人释放新租约。同资源竞争返回 CONFLICT。锁为建议性，不阻止外部进程；目录范围锁不自动锁子文件，应采用同一具体文件/设备键，调用者统一资源命名。

`handoff_export(session_id,package_id,expected_version)` 返回本地审计 handoff_id 与 sha256；新对话 session_open 后 `handoff_resume(session_id,handoff_id,sha256)` 显式附加。只支持同 canonical 项目和同本地 DB；工作包已变更则 HANDOFF_STALE。新对话必须自行确认 intake 才能修改工作包，不复制个人选择。handoff 不用于授权危险操作。

只读 Resources：`scs://session/{session_id}`、`scs://work-package/{package_id}`。本地服务无用户认证，知道 ID 的同一项目客户端可读取这些资源；不适用于不互信用户。

## 文件同步与回退

SQLite 是结构化状态源；AGENT_PROJECT.md、AGENT_COORDINATION.md 是主对话维护的可审查记忆，evidence 是原始证据，04_test/report/ 是审查报告。服务仅创建缺失记忆文件，所有变更返回 record_sync_required；主对话将修订、结果、证据索引和 handoff 追加到既有记录，发现用户编辑冲突时停止并核对。

MCP 断开：纯 Skill 继续合法文本交互，工作包/修订/证据按原路径维护，状态如实记录。MCP 恢复：核对 session_get/work_package_get 与手工记录；显式决定采用文件或 DB，必要时创建新修订后逐项录入。没有自动导入器，不能覆盖用户改动。迁移/移动整个项目后 DB 会因 canonical 根不匹配拒绝，先备份，通过审核的工作包定义和证据手工恢复到新根，不复制旧会话确认。


## Obsidian 项目视图

新增 project_view_refresh / project_view_info，仍是单个 MCP 服务、共 17 个工具；`interaction_ask` 提供 legacy form 的服务端超时。生成、自动刷新、首次打开、批注保留与升级边界见 [obsidian.md](obsidian.md)。
