# Codex 第五轮复审 — host-adaptive（fa1b23e）ACCEPT

- 基线：`host-adaptive-questions @ fa1b23e`，工作树干净；与 `fc43561` 相比只有 README/validation/report 三份文档变化，代码/测试哈希一致。
- 审查者：Codex，`gpt-6.1-sol`，二进制 `0.167.0-alpha.3.2`，只读沙箱，high。
- 会话：`01a1c6f0-52e6-73b1-898d-1a4db7f91edc`。
- 结论：**ACCEPT**（限本次静态复审范围）。
- 原始日志：`/tmp/codex-review-scs-host-adaptive-5.log`；最终消息：`/tmp/codex-review-scs-host-adaptive-5.md`。

## 逐项

1. **[P3] 流程图缺失出口 — FIXED**：cancel/decline→保持未确认、native 不可用→文本已补齐；accept→确认→执行、TIMED_OUT→推荐项继续且不确认、RPC_ERROR→FAILED/显式重开、无 form/modern→文本均在图中；未发现遗漏分支。
2. **[P2] host/client 命名空间碰撞 — FIXED**：JSON 数组 + SHA-256 前缀保留字段边界；跨宿主隔离、同宿主幂等、歧义 pair 测试仍在。
3. **[P2] 下游长度/字符约束 — FIXED（静态）**：传给 `state.open` 的名称固定为 `h` + 32 hex，符合约束。
4. **[P2] RPC 异常记账及持久化断言 — FIXED（静态）**：共享 FAILED/RPC_ERROR；测试通过 `session_get` 断言持久化 FAILED。
5. **[P1] modern fallback 缺问题身份 — FIXED**：先 prepare 再判断 modern，保留 question_id；modern/无 form 测试均直接 submit。
6. **[P1] 默认路由与首次提问冲突 — FIXED**：只允许一次必要 `session_open`，随后按 channel 路由；worker 继承选择、不重新 intake。
7. **[P2] 安装期宿主选择及示例 — FIXED/可用（静态）**：codex→native、zcode→mcp；生成配置、入口和文档 env 一致。
8. **[P2] Codex 版本能力边界 — FIXED**：明确区分原生/文本可用与 MCP form 未验收；本机未注册 SCS MCP。
9. **[P3] ZCode compat 与无 form 回落 — 可用（静态）**：协议与 channel 独立；legacy 仍检查 form 声明，无 form 保留文本提交。
10. **[P2] 同一实现支撑多宿主 — 可用（静态、有边界）**：**可以用同一套实现支撑 ZCode legacy MCP 15s 与 Codex native/text，无需拆主对话/子 Agent 两套流程。** 主对话负责交互，worker 继承授权；真实宿主 UI 仍待验收。

新问题 P0–P3：**未发现可确证的新问题**。

运行验证：本轮 NOT_RUN；133 项 pytest（9 项 host-channel）、PyQt live 17 工具、安装器及安装版超时探针均为外部材料，Codex 未核对原始日志。ACCEPT 限于本次静态复审范围。
