# Codex 第三轮复审 — host-adaptive（bc5e79f）

- 基线：`host-adaptive-questions @ bc5e79f`，工作树干净。
- 审查者：Codex，`gpt-6.1-sol`，二进制 `0.167.0-alpha.3.2`，只读沙箱，high。
- 会话：`01a1c6e5-e86c-7542-b977-11bc627c6053`。
- 结论：**REJECT**（默认 Codex/ZCode 路径已成立，拒绝点为身份命名空间可碰撞）。
- 原始日志：`/tmp/codex-review-scs-host-adaptive-3.log`；最终消息：`/tmp/codex-review-scs-host-adaptive-3.md`。
- 运行材料：132 项 pytest、8 项 host-channel、安装器、PyQt live 17 工具、安装版超时探针均仅文字声称，Codex 未核对日志；Codex 内 SCS MCP/form 渲染 NOT_RUN。

## 逐项

1. **[P2] 默认会话命名空间 — FIXED（静态）/ PARTIAL**：host 已进入身份参数，默认 `codex-codex`、`zcode-zcode`；同 host 同 ID 幂等、跨 host 不同 ID 的测试存在。任意配置值仍有碰撞问题。
2. **[P2] client_name 校验/长度 — PARTIAL**：入口没有独立长度约束；拼接后长度增加；下游校验未在本轮只读清单内，不能宣称无回归。
3. **[P2] README 宿主 env 示例 — FIXED**：Codex TOML 已有 `SCS_HOST=codex`、`SCS_QUESTION_CHANNEL=native`；ZCode 示例有 `zcode/mcp`。
4. **[P1] 原生优先与 host channel 冲突 — FIXED**：README 顶部/流程图、interaction_request docstring、SKILL/AGENTS/mcp.md 均按 channel 路由；worker 不重新 intake。
5. **[P2] RPC 异常记账及测试 — FIXED（静态）/ 执行 NOT_RUN**：callback 抛 RuntimeError，源码走共享 FAILED/RPC_ERROR 处理；测试断言 TEXT_FALLBACK/RPC_ERROR/qid，但未直接断言持久化状态为 FAILED。
6. 上轮其余项 — FIXED/可用（静态）：modern fallback、Codex 版本限制、安装期选择、legacy 无 form、ZCode compat。
7. **[P2] host/client 拼接仍可碰撞 — NOT FIXED**：`host-client_name` 不是无歧义编码；host=`codex` + client=`dev-ui` 与 host=`codex-dev` + client=`ui` 都得到 `codex-dev-ui`；非法字符替换/32 字符截断也会合并配置值。
8. **[P3] 流程图缺 form 成功出口 — NOT FIXED**：`interaction_ask` 只有超时/失败出口，没有正常 accept 到确认/执行；RPC_ERROR 后 reopen 也未画出。
9. **[P2] 同一实现支撑多宿主 — 可用（静态、有边界）**：可用同一套实现支撑 ZCode legacy MCP 15s 与 Codex native/text，不需要拆两套主/子流程；任意 host 配置隔离仍需修复，真实宿主 UI 未验收。

未发现新的可确证 P0/P1；拒绝点为第 7 项身份碰撞。132 项 pytest、8 项 host-channel、安装器、PyQt live 17 工具、安装版超时探针均保留 NOT_RUN/待核对。

结论: REJECT
