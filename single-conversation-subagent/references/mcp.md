# MCP 用法与恢复

纯 Skill 无 Python/MCP 依赖。混合模式只在工具可见时使用；可见不代表模型一定调用，也不证明 UI 显示。服务无原生 Agent 调度工具。

启动：`python -m scs_mcp --project-root /absolute/project`。仅 stdio；项目根是启动时明确允许的边界，工具不能换根。状态为该根下 `.scs/state.sqlite3`，SQLite WAL，schema 2，busy timeout 3 秒。只支持本地单用户项目；不提供认证或跨用户服务。

## 初次交互

1. `session_open(project_root=..., client_name="host", conversation_id="宿主真实对话ID", review_registration="registered|unregistered|unknown")`。ID 未知时省略并保留返回 ID，每次省略都会新建临时会话。不得伪造真实 ID、用项目路径当对话 ID。
2. 原生工具可用时，直接用原生提问。若需要 MCP 记账，先 `interaction_request(session_id=...,kind="mode",recommendation=["long"],channel="native")`，然后真实调用原生工具，将用户明确答复以返回 question_id 提交。服务不越过宿主调用 UI。
3. 原生不可用时 `interaction_request(...,channel="auto")`，由实际连接声明能力决定能否用 MCP。Resolver/Elicit 兼容现代多轮和 legacy 回调。服务端不按客户端名称猜测。
4. TEXT_FALLBACK_REQUIRED：显示 display_text 中完整选项与唯一推荐，结束当轮等回复；再 `interaction_submit(session_id=...,question_id=...,action="accept",choices=["long"],source="text")`。不把文本当原生弹窗成功。
5. `session_get` 读取已确认选择与未决问题。同题重复 request 不弹第二个 UI，已确认直接返回 ALREADY_CONFIRMED。

模式值 `quick/standard/long/investigate`。推荐是单元素列表。仅首次进入 standard/long 后请求 verification，值为 `smoke/targeted/full/stability/none/method-only`，支持多选；推荐只有一个列表组合。`none` 和 `method-only` 各自排除所有其他项。冒烟本轮，定向/全量/稳定性收尾；用户指定预算、资源、方法写入协调记录。危险操作不在此表单授权范围。

verification 确认后，只有宿主确证注册 codex-review 才请求 codex_review，值 `no/yes`，唯一推荐 `["no"]`。未知/未装返回 SKIPPED。`explicit_skip=true` 仅用于用户明确“不用问，直接做”，记录 quick；需要改变模式时显式 reopen，不把自动检测当用户跳过。

## 问题与故障

`interaction_submit` 接受 accept/decline/cancel/timeout/unsupported/error。accept 必须有明确合法选择。timeout 需要宿主可观测超时回执 `observed_timeout=true`，仍不自动选项。RPC 超时提交 error，不能填 timeout。decline/cancel/timeout 保持终态未确认。

重试或用户明确改变时用 `interaction_reopen(session_id,kind,explicit_user_change=...)`；活动/已确认问题必须有用户明确变更。然后 request 获取新 question_id，旧回复返回 LATE_REPLY。原生失败时先记录 unsupported/error，再 reopen，最后请求 auto；MCP 失败后可直接按同题显示文本并 submit。客户端重启使 SDK 密封 requestState 失效时，核对 session_get 的未决题，显式 reopen，再请求；不要复用旧响应。

服务可能观察 REQUESTED/WAITING_USER，不能证明已渲染；统一 UI_NOT_OBSERVABLE，真实接受回执只证明客户端提交了内容。模型可能未调用工具，客户端也可能自动生成答案；最终应以宿主实际用户交互记录核对。

`diagnostics_check(session_id=...)`：版本、DB quick_check、工具注册、协议、声明能力、最近 30 条脱敏事件。SKILL_NOT_LOADED 需宿主加载证据；TOOL_NOT_CALLED 需宿主调用日志。RPC_ERROR 与 ELICITATION_UNSUPPORTED 可在服务观察；用户取消/拒绝/接受分别 USER_CANCELLED/USER_DECLINED/USER_CONFIRMED。stdout 仅协议；默认错误日志 stderr，不启用 SDK 调试日志以免写入调用输入。

## 工作包、证据与租约

小问题无需工作包。创建示例：

```json
{"session_id":"S-...","goal":"修复解析器","allowed_files":["src/parser.py"],"acceptance":["空输入不异常"],"request_key":"parser-1","subtasks":[{"id":"T1","kind":"test","allowed_files":["src/parser.py"],"acceptance":["定向用例通过"]}]}
```

传给 work_package_create，返回唯一 WP ID、revision=1、version=1、docs_root 和待同步记录路径。request_key 在同对话内幂等；不同定义同键冲突。subtask kind 为 analyze/implement/test。文件范围为明确相对文件/目录，不是 glob；子任务不可扩大父范围。服务不授予文件或执行权限。

`work_package_update` 需 expected_version，成功加一，旧版本 VERSION_CONFLICT；不要盲重试。状态 PENDING → IN_PROGRESS → REVIEW_READY，BLOCKED 可恢复为 IN_PROGRESS。结果 PASS/FAIL/BLOCKED/NOT_RUN/SUBSTITUTED；PASS 是子任务证据报告，不是最终接受。new_revision=true 单独调用，清空结果并分配新证据目录，不删除旧文件。

证据路径 `<docs-root>/agent/evidence/WP-.../R1/T1/result.json`。`evidence_verify(session_id,package_id,revision,subtask_id,path,required_content=[])` 返回 VERIFIED/INVALID/INSUFFICIENT。UTF-8、非空、普通文件、最大 2 MB；测试证据 JSON 必须有非空 command、整数 exit_code=0、正整数 passed。不能证明命令真实执行；主对话核原始日志和环境。PASS 更新引用 `{path,sha256,required_content?}`，更新时再次验证，内容变动则拒绝。未测写 NOT_RUN；不把测试方法当通过。

`resource_acquire(session_id,resource="file:src/parser.py",ttl_seconds=120)`；资源类型 file/device/serial/programmer。续租携带 lease_token，释放也须 token；过期新 token 防止旧持有人释放新租约。同资源竞争返回 CONFLICT。锁为建议性，不阻止外部进程；目录范围锁不自动锁子文件，应采用同一具体文件/设备键，调用者统一资源命名。

`handoff_export(session_id,package_id,expected_version)` 返回本地审计 handoff_id 与 sha256；新对话 session_open 后 `handoff_resume(session_id,handoff_id,sha256)` 显式附加。只支持同 canonical 项目和同本地 DB；工作包已变更则 HANDOFF_STALE。新对话必须自行确认 intake 才能修改工作包，不复制个人选择。handoff 不用于授权危险操作。

只读 Resources：`scs://session/{session_id}`、`scs://work-package/{package_id}`。本地服务无用户认证，知道 ID 的同一项目客户端可读取这些资源；不适用于不互信用户。

## 文件同步与回退

SQLite 是结构化状态源；AGENT_PROJECT.md、AGENT_COORDINATION.md 是主对话维护的可审查记忆，evidence 是原始证据，04_test/report/ 是审查报告。服务仅创建缺失记忆文件，所有变更返回 record_sync_required；主对话将修订、结果、证据索引和 handoff 追加到既有记录，发现用户编辑冲突时停止并核对。

MCP 断开：纯 Skill 继续合法文本交互，工作包/修订/证据按原路径维护，状态如实记录。MCP 恢复：核对 session_get/work_package_get 与手工记录；显式决定采用文件或 DB，必要时创建新修订后逐项录入。没有自动导入器，不能覆盖用户改动。迁移/移动整个项目后 DB 会因 canonical 根不匹配拒绝，先备份，通过审核的工作包定义和证据手工恢复到新根，不复制旧会话确认。


## Obsidian 项目视图

新增 project_view_refresh / project_view_info，仍是单个 MCP 服务、共 16 个工具。生成、自动刷新、首次打开、批注保留与升级边界见 [obsidian.md](obsidian.md)。
