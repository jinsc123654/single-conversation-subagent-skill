---
name: single-conversation-subagent
description: 单对话两级 Subagent 协作；每个真实对话首个任务选择执行模式，标准/长任务确认验证范围；支持原生提问、可选 MCP 状态与纯文本回退。
---

主对话是唯一协调者，维护阶段记忆、直接派发宿主原生执行子 Agent 并最终验收。子 Agent 只分析/实现/测试，不继续委派。MCP 只处理交互、状态和证据，不创建或调度 Agent。

每个真实对话首次任务选四项：快速处理 / 标准执行 / 长任务 / 只调查；必要只读调查后按范围、风险、成本只给一个动态推荐。默认勾选不是用户确认。后续沿用本对话选择，不按 cwd、PID、MCP 连接或旧工作包复用其他对话的选择。用户明确改变方式时按新要求调整；仅明确“不用问，直接做”可跳过→快速，危险操作仍另确认。**四选一前不读项目配置/记忆、不检查 MCP 注册或项目根；只允许一次 `session_open`（不带 project_root，服务端用配置根）或原生/文本提问，然后立即四选一；答复后再做必要只读调查。**

交互按 `session_open` 返回的 `question_channel` 路由：
1. `native`：调用宿主原生 Ask/Question；没有时用文本。
2. `text`：完整展示选项、唯一推荐与 question_id，结束当轮等待明确回复。
3. `mcp`：先 `interaction_ask(timeout_seconds=15)`；返回 `MODERN_NO_BACKCHANNEL`/`ELICITATION_UNSUPPORTED`/`TEXT_FALLBACK_REQUIRED` 时，用同一 `question_id` 改走原生/文本，明确回复后直接 `interaction_submit`。
4. `auto`：MCP form 可用则按 `mcp`，否则按 `native`/`text`。
原生/文本路径不声称 15 秒；15 秒只属于 `mcp` form 路径。MCP 路径先 `session_open` 绑定宿主真实 conversation ID（可省略 project_root）；原生提问不需要 session_open。

同一时刻只用一路提问。原生已提问时，不再打开 MCP 表单；若用 MCP 记账，interaction_request(channel="native") 只登记，再将真实回复 interaction_submit。原生失败后先记录失败，显式重开问题再换下一路。确认过的选择不重复问；取消/拒绝保持未确认，只有用户要求重试或改变时显式重开。超时仅认可宿主真实可观测结果且事先告知的配置策略；`interaction_ask` 的服务端截止时间是可观测结果，到点标记 `TIMED_OUT` 并返回推荐项；命令等待、RPC 超时、UI 不出现均不是用户超时。fallback（native/text、无 form、modern 无回通道）保持问题 `WAITING_USER`，可直接 `interaction_submit`；RPC 异常才标 `FAILED`，需显式 reopen 后重试。`TIMED_OUT` 不写 `CONFIRMED`，主对话可按本对话策略采用返回的推荐项继续。ZCode 原生 AskUserQuestion 自动解析为 **60 秒隐藏 + 5 分钟截止（空答案）**，不是 15 秒；15 秒路径默认用 `interaction_ask`。工具已调用不能证明用户看见 UI。

仅本对话首次进入标准/长任务再依次确认：
- 验证范围：一个动态推荐组合；本轮冒烟、收尾定向/全量/稳定性按实际风险与预算选择。未选项不执行。“不做测试”与“只记录方法”互斥且排除测试，均 NOT_RUN；后者只记录方法，不标 PASS。已有明确授权的测试范围可记录并沿用，不重复请求同一授权。破坏性操作独立确认。
- 收尾独立审查：明确检测到注册的 codex-review 才询问使用/不使用，唯一推荐不使用。未知/未装跳过；用户已明确要求时直接沿用，单工作包/修订只审一次。主对话启动外部只读审查，子 Agent 不启动。

快速直接做、不建子 Agent；只调查只读，范围大可派只读分析子 Agent；两者不强制初始化工作包。执行子 Agent 继承主对话已确认的 mode 和 verification，不重新四选一、不打开提问、不扩大范围；主对话是唯一用户交互入口。标准/长任务及需要恢复、证据、并发时按需读 [完整协议](references/protocol.md)，MCP 参数及故障诊断读 [MCP 用法](references/mcp.md)。MCP 断开时按同一策略维护文件和文本交互；恢复前显式核对 SQLite 与手工记录，不自动覆盖/导入。

docs-root 优先 00_doc / 00_docs / docs / doc，均无创建 docs。AGENT_PROJECT.md、AGENT_COORDINATION.md、evidence/WP-<id>/R<rev>/<subtask-id>/ 在 docs-root/agent/；WP 审查报告在 04_test/report/。不迁移旧文件。主对话维护可审查 Markdown，MCP 只初始化缺失记录并返回同步责任；冲突停下核对。没有可靠证据不得标 PASS，最终接受由主对话决定。


用户要求用 Obsidian 管理项目时，按需读 [Obsidian 视图](references/obsidian.md)。每个项目 docs-root 作为独立库根，project_view_refresh 生成 agent/acceptance/ 浏览页；默认启用后的 MCP 工作包变化自动刷新。project_view_info 返回打开链接及过期/冲突检查。保留批注、源记忆和现有 .obsidian 配置；无插件，不改全局库注册，不把子任务通过当最终验收。首次须在 Obsidian 打开该文档文件夹为库；MCP 返回链接不证明软件已打开。纯 Skill 仍可维护 Markdown，恢复前显式核对；未要求 Obsidian 的小问题不额外初始化视图。
