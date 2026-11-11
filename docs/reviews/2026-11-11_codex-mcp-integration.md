# Codex 宿主 MCP 集成实测（2026-11-11）

- 宿主：Codex `0.167.0-alpha.3.2`，模型 `gpt-6.1-sol`，会话 `01a1c7f2-5488-7693-9699-b00fac62470f`。
- 配置：`~/.codex/config.toml` 新增 `[mcp_servers.single-conversation-subagent]`，命令指向本机运行时 `venv.IVPAWU/bin/python` + `launcher.py`，根 `vela_sifli`；env `SCS_HOST=codex`、`SCS_QUESTION_CHANNEL=native`；备份 `config.toml.bak-scs-mcp-20261111-080811`。
- 测试命令：
  ```bash
  codex exec -m gpt-6.1-sol --approve-for-me -C <repo> \
    "Use the single-conversation-subagent MCP tool diagnostics_check once..."
  ```
- 结果：MCP 工具 `single-conversation-subagent/diagnostics_check` **started → completed**；返回 `status: OK`、`registered_tools: 17`、`server_version: 2.2.0`、`protocol_version: 2025-06-18`。
- 审批边界：默认 `codex exec` 的 `approval: never` 会阻止 MCP 工具调用（`MCP tool call requires approval, but approval policy is never`）；`--approve-for-me` 下自动审批完成。交互式 Codex 会话应按其正常审批流程确认 MCP 工具。
- 通道边界：Codex 使用 `SCS_QUESTION_CHANNEL=native`，`session_open` 会返回 `question_channel=native`；`interaction_ask` 的 native 路径只返回 `NATIVE_QUESTION_REQUIRED` + question_id，不打开 MCP form。Codex 原生 question UI 在本次 headless 测试中未实际弹窗，标 NOT_RUN。
- 协议：Codex 协商 `2025-06-18` legacy；SCS 的 `zcode_compat` 只强制 ZCode 回落，Codex 走正常 legacy 路径，工具调用正常。
- 结论：**Codex 可以用同一套 SCS 运行时调用 MCP 工具**；默认审批策略需要交互批准或 `--approve-for-me`，这是宿主策略，不是服务端失败。
- 日志：`/tmp/codex-scs-mcp-test-3.log`；最终消息：`/tmp/codex-scs-mcp-test-3.md`。
