# Codex 使用性审查 — single-conversation-subagent / interaction_ask

- 审查对象：Skill + 本地 MCP 的 Codex 可用性，重点是 `interaction_ask(timeout_seconds=15)` 与“MCP form 优先”默认路由。
- 基线：`main 851c2e6`（前序 `373abe4`、`531c6de`），工作树干净。
- 审查者：Codex，模型 `gpt-6.1-sol`，二进制 `0.167.0-alpha.3.2`，只读沙箱，推理档位 `high`。
- 会话：`01a1c6cc-49e1-7852-aa95-722c5d34c199`。
- 结论：**REJECT**。
- 原始日志：`/tmp/codex-review-scs-design.log`；最终消息：`/tmp/codex-review-scs-design.md`。
- 运行材料：128 项 MCP pytest、4 项 interaction_ask 测试、PyQt live 17 工具、安装版 stdio 超时探针，均为外部提供、Codex 未核对原始日志。Codex 内 SCS MCP 注册、握手能力、form 渲染及真实回答链路均为 **NOT_RUN**。

## 基线哈希

```text
8859c851238e722f7e79702d30c32d310928bde3acc5151d592accb3f61588bc  mcp_server/src/scs_mcp/server.py
1287c6b16fc0dc214436de029f97de6b6c183cf1be93a4e1fdcc437a629567b7  mcp_server/src/scs_mcp/zcode_compat.py
89b96d9bfd4383cbe42b480c1d5a7630c1ce8d275663e44a081c449e941a58c5  single-conversation-subagent/SKILL.md
76e230f46e305e8fedf24cafe8ae5d392250953292d4cc504a9604cabffa5600  single-conversation-subagent/references/mcp.md
f953a2e520a9d320803f7944d9a36f947968b67a2d5709b57991a3d838a1aafe  AGENTS.snippet.md
1cc7fbcb1af6d725862293ffb639bef5af235486ff85846a2e0b52e614d2d107  README.md
add8bce9abd2279157fcef168692edc9f4bc310d98232c29f9bba573ec624893  mcp_server/tests/test_interaction_timeout.py
```

## Codex 最终结论（原文）

**Codex 能使用这个架构的原生提问／文本回落路径；当前实现尚不能作为 Codex 下可靠的“MCP 表单优先”方案验收。**

基线核对一致：`main 851c2e6`、工作树干净、七项源码哈希全部匹配。未修改文件、构建、联网、委派或运行测试。

1. **[P2] Codex 精确版本的 form/back-channel 能力证据不足 — PARTIAL — [README.md:118]**

   现有材料不能证明 `0.163.0-alpha.10.2` 支持，也不能据此判定它不支持 `elicitation/create(form)`。能力矩阵将桌面聊天和 CLI 合并，但本服务在两者中的集成都未验证。

   对本机二进制的静态检查发现 `elicitation/create`、Form 请求类型及 stdio elicitation 处理相关标识，同时发现嵌入的 `codex-mcp-client/0.167.0-alpha.3.2`。这不能代替目标版本的握手和 UI 证据。

   当前会话有 `request_user_input_async`；同步 `request_user_input` 的工具说明受 Plan 模式限制。不能统一假设存在名为 Ask/Question 的工具，也不能将当前会话能力外推到该 CLI 版本。完整文本四选一、结束当轮、等待用户下一轮回复可用；无人值守的 `codex exec` 无法靠文本自动取得回答。

2. **[P2] legacy 无 form 可以回落，但 MCP 优先会增加往返 — 可用**

   若 Codex 使用 legacy 且未声明 form，`interaction_ask` 会经 `prepare` 将问题标为 `UNSUPPORTED`，返回带 `ELICITATION_UNSUPPORTED` 的文本回落描述；不会调用 `ctx.elicit`，不会等满 15 秒，也不会写入 `CONFIRMED`。

   此时可以展示文本，并用同一 `question_id` 提交用户真实回答。测试源码包含这条恢复路径，但本次未执行。UNSUPPORTED 本身不意味着永久阻塞；必须先完成真实回答的提交，再进入后续 verification/codex_review 流程。切换原生路径时应遵守显式 reopen 规则，不能假设修改 channel 就会重开表单。

3. **[P1] modern 回落没有登记问题，文档提交链无法直接完成 — 不可用**

   modern 分支在 `prepare` 前直接返回 `TEXT_FALLBACK_REQUIRED / MODERN_NO_BACKCHANNEL`：没有创建问题、没有 `question_id`，也没有执行已有确认／终态检查。

   因而不能照文档“显示文本后直接 `interaction_submit`”。调用方必须额外调用 `interaction_request(channel="text"|"native")` 取得问题身份；当前回落说明没有完整规定这一步。

   最小修复：先完成会话校验、问题登记及幂等检查，再决定传输路径；统一返回 `question_id`、问题状态和回落原因。`MODERN_NO_BACKCHANNEL` 只表示该限时工具不能使用回调，不应解释为客户端完全不支持 elicitation。

4. **[P2] interaction_ask 的 RPC 异常漏记 FAILED — PARTIAL**

   普通异常的失败记账只覆盖 `interaction_request`。`interaction_ask` 中 `ctx.elicit` 抛出非超时异常时，会返回通用 `TOOL_ERROR`，没有相同的 `FAILED/RPC_ERROR` 处理，也没有保证返回 `question_id`。

   问题可能留在等待状态；再次调用又会被 `prepare` 的重复提问保护拦住。文本仍可能恢复，但不能声称“调用失败均已正确记录”。

   最小修复：两种提问工具共享异常处理，保留问题身份并记录 FAILED；用户取消、拒绝和真实超时保持各自终态。

5. **[P1] 默认路由与首次提问规则互相冲突 — 不可用**

   Skill/AGENTS 禁止四选一前调用工具、检查 MCP 或项目根，却又要求优先调用需要 `session_id` 的 MCP 工具。与此同时，README 写原生优先，Skill 写 MCP 优先，参考文档两种顺序并存。宿主无法获得唯一、可执行的默认流程。

   最小修复：统一为按宿主能力选择：可用原生 → 已确证适用的 MCP form → 文本。首次会话尚未绑定时先原生／文本提问，随后登记真实回答；或明确允许必要的最小 MCP 初始化调用。

6. **[P2] 宿主适配应由安装配置选择，无需拆两套完整流程 — 可用**

   明确结论：需要宿主适配和安装期选择，不需要把策略拆成主对话、子 Agent 两套实现。主对话负责用户交互；worker 继承已授权的模式和验证范围，不重新四选一。子 Agent 不能补足宿主缺失的 UI 能力。

   最小改动清单：

   - 安装配置增加 `host`、`question_channel=auto|native|mcp|text`，输出实际采用的路由及能力未知项。
   - 运行时结合真实工具是否可调用、MCP 协议和 form 声明决定路径；未知能力直接回落。
   - 原生适配记录实际工具名及模式限制；15 秒策略只用于支持限时回调的路径。
   - 增加 worker 跳过 intake 的明确规则。
   - 同步 Skill、AGENTS、参考文档和 README；修复第 3、4 项状态处理。

7. **[P3] ZCode 兼容层不能作为 Codex 表单能力保证 — PARTIAL**

   明确标识的非 ZCode modern 客户端保持 modern；无身份探测也会被降为 legacy。legacy 协议不等于声明了 form，更不等于 UI 已显示。

   本仓库确已受信任，但本机没有 SCS MCP 配置，因此当前无法调用它。源码测试中的无 form 用例使用 `mode="auto"`，也不能单独证明覆盖了 legacy 无 form 分支。

外部声称的 128 项 pytest、4 项限时交互测试、PyQt 17 工具及安装版超时探针，均保留为待核对材料；本次未核对原始运行日志。Codex 内 SCS 注册、握手能力、form 渲染及真实回答链路均为 NOT_RUN。

结论: REJECT
