# Codex 第四轮复审 — host-adaptive（fc43561）

- 基线：`host-adaptive-questions @ fc43561`，工作树干净。
- 审查者：Codex，`gpt-6.1-sol`，二进制 `0.167.0-alpha.3.2`，只读沙箱，high。
- 会话：`01a1c6ec-8763-74b2-a20b-f0b548847ee8`。
- 结论：**REJECT**（仅剩 README 流程图 P3；其余 P1/P2 已 FIXED）。
- 原始日志：`/tmp/codex-review-scs-host-adaptive-4.log`；最终消息：`/tmp/codex-review-scs-host-adaptive-4.md`。

## 逐项

1. **[P2] host/client 拼接碰撞 — FIXED**：JSON 数组编码 + SHA-256 前缀，保留字段边界；歧义 pair 测试比较 `codex/dev-ui` 与 `codex-dev/ui` 的 session ID；同宿主幂等、跨宿主隔离已覆盖。
2. **[P2] 下游长度/字符约束 — FIXED（静态）**：传给 `state.open` 的 client_name 固定为 `h` + 32 hex，共 33 字符，满足 `[\w .-]{1,64}`；raw host/client 不再直接传入。
3. **[P3] 流程图出口完整性 — PARTIAL**：form accept、RPC_ERROR、无 form/modern 已补齐；仍缺 `interaction_ask` 的 cancel/decline 出口，以及“原生不可用→文本”路径。
4. **[P2] RPC 异常记账及持久化断言 — FIXED（静态）**：测试断言 `RPC_ERROR` + qid，并通过 `session_get` 断言持久化 `FAILED`。
5. **[P1] modern fallback 无问题身份 — FIXED**：先 prepare 再判断 modern，保留问题身份；modern/无 form 测试均直接 submit。
6. **[P1] 默认路由与首次提问冲突 — FIXED**：只允许一次 `session_open`，按 channel 路由；worker 继承、不重新 intake。
7. **[P2] 安装期宿主选择 — FIXED/可用（静态）**：codex→native、zcode→mcp，env 示例一致，入口读取。
8. **[P2] Codex 版本能力边界 — FIXED；本机未注册 SCS MCP**。
9. **[P3] ZCode compat 与无 form 回落 — 可用（静态）**：协议与 channel 独立，legacy 不保证 form。
10. **[P2] 同一实现支撑多宿主 — 可用（静态、有边界）**：同一套实现支撑 ZCode MCP 15s 与 Codex native/text，不需要拆主/子两套流程；真实宿主 UI 待验收。

未发现新的 P0–P2；拒绝点仅为第 3 项流程图完整性。133 项 pytest、安装器、PyQt live 17 工具、安装版超时探针均未核对原始日志，保留 NOT_RUN/待核对。

结论: REJECT
