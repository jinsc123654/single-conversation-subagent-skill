# Codex 复审 — host-adaptive questions（fa17c97）

- 基线：`host-adaptive-questions @ fa17c97`，工作树干净。
- 审查者：Codex，`gpt-6.1-sol`，二进制 `0.167.0-alpha.3.2`，只读沙箱，high。
- 会话：`01a1c6dd-49f9-75b1-82f5-50eae583874f`。
- 结论：**REJECT**（核心修复成立，但仍有路由残留和宿主隔离缺口）。
- 原始日志：`/tmp/codex-review-scs-host-adaptive.log`；最终消息：`/tmp/codex-review-scs-host-adaptive.md`。
- 运行材料：130 项 MCP pytest、6 项 host-channel、安装器、PyQt live 17 工具、安装版超时探针均仅文字声称，Codex 未核对原始日志；Codex 内 SCS MCP/form 渲染 NOT_RUN。

## 逐项

1. **[P1] modern fallback 无 question_id — FIXED**：先 `prepare` 再判断 modern，`describe(MODERN_NO_BACKCHANNEL)` 保留 `WAITING_USER` + `question_id`；测试直接 submit。
2. **[P1] 默认路由冲突 — PARTIAL**：Skill/AGENTS/mcp.md 已按 channel 路由，首次只允许一次 `session_open`，worker 不重新 intake；但 README 顶部/流程图仍写“原生优先”，`interaction_request` docstring 仍说 native 可用时用 native，与 `mcp` channel 冲突。
3. **[P2] interaction_ask RPC 异常记账 — FIXED；测试 NOT_RUN**：`_call_tool` 覆盖两个提问工具，标记 FAILED/RPC_ERROR 并返回问题身份；所列测试未覆盖异常分支。
4. **[P2] Codex 精确版本能力 — FIXED**：README 改为 `0.167.0-alpha.3.2`，SCS MCP NOT_RUN，不假设所有 Codex 有 Ask/Question。
5. **[P2] 安装期宿主选择 — FIXED（静态）/ 可用**：`--host`/`--question-channel` 默认 codex→native、zcode→mcp；示例写入 env 与 host-profile；入口读取 env；`session_open` 返回 channel/host。临时安装结果本轮 NOT_RUN。
6. **[P3] legacy 无 form 测试 — FIXED**：`test_legacy_no_form...` 使用 `mode="legacy"`、无 callback，断言 `ELICITATION_UNSUPPORTED` + qid + 直接 submit；执行 NOT_RUN。
7. **[P3] zcode_compat 与 host channel — 可用（静态）**：协议 era 与提问 channel 独立；legacy 不代表声明 form，运行时仍检查 form 能力。

## 新问题

- **[P2] 默认会话命名空间没有使用配置宿主 — 不可用（默认跨宿主隔离）**：`client_name` 默认固定 `"host"`，配置的 host 只附加到返回值，没有参与 `state.open`；同一项目、Codex/ZCode 省略 client_name 且 conversation_id 相同时会命中同一 identity。修复：host 前缀参与会话命名空间，并补跨宿主同 ID 用例。
- **[P2] 手写 Codex 配置示例遗漏宿主 env — 不可用（预期 native 默认路由）**：README 的 Codex TOML 没有 `SCS_HOST=codex`、`SCS_QUESTION_CHANNEL=native`；直接复制得到 generic/auto。安装器生成的 TOML 正确，README 示例需同步。

未发现新增可确证 P0/P1。外部运行的 130 项 pytest、6 项 host-channel、临时安装器、PyQt live 17 工具、安装版超时探针均保留 NOT_RUN/待核对。

结论: REJECT
