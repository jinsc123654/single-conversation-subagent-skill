# ZCode 子 Agent 上下文判断

1. 从实际派发回执取得目标 `agent_...` ID 和它的宿主父会话 `sess_...` ID；不要用 SCS 会话 ID、角色名或主对话模型代替。续跑前等候真实完成回执，不向运行中的 Agent 发送消息来测容量。
2. 在当前主对话调用 `ListModels`，然后运行 Skill 随附的 Node.js 24.2+ 只读探针。工具文本可能不显示窗口字段，探针读取 5 分钟内同一调用的结构化宿主记录，并按精确 `provider/model` 匹配。

```bash
node "$HOME/.zcode/skills/single-conversation-subagent/scripts/zcode-context.mjs" \
  --parent 'sess_实际父会话' --agent 'agent_实际派发ID'
```

如果在当前对话核查别的父会话，额外传 `--catalog-session 'sess_当前对话'`，让探针用当前对话刚查的模型目录；`--parent` 仍是目标子 Agent 的真实父会话。历史查询得到的是最后一次已完成请求的用量，以及按当前宿主窗口换算的比例；不要把当前窗口冒充未经验证的历史配置。

- `used_percent` 按最近一次已完成模型请求的 `input_tokens + output_tokens` 计算。缓存输入已经包含在输入 token 中，不重复相加；不用累计账单或主会话用量。
- `projected_percent` 另加下一轮余量（默认 4096 token；预计更多时用 `--reserve N` 提高）。预计占用 **超过 70%** 或宿主明确记录上下文超限时是 `ROTATE`；恰好 70% 仍为 `REUSE`。比较用未舍入的 token 数。
- `UNKNOWN`（如窗口未持久化、模型不匹配、记录不可核验或脚本不可用）不代表 0%。报告能核验的数字并说明缺项；如需继续，直接新开同角色。`REUSE` 只表示容量允许，不代替权限、基线、工作包和正式审查的独立性检查。

例：最后一次请求 354,516 / 1,000,000 为 35.45%；加 4,096 余量后预计 35.86%，容量允许续跑。若下一轮是正式收尾审查，则仍新开固定审阅员实例。
