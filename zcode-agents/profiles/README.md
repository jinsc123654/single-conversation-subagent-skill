# 已部署 ZCode Agent 档案快照

本目录收录参考机 `~/.zcode/agents/` 实际部署的 6 个档案(2026-11-24 按用户要求完成权限与思考档位升级)，是本 Skill 在 ZCode 宿主上的配套子 Agent 设置：

| 档案 | 模型 | thoughtLevel | 工具 | 职责 |
|---|---|---|---|---|
| 方案顾问.md | new-provider-2/gpt-6.2-sol | enabled（该模型最高档） | 全部（Read/Grep/Glob/Bash/Edit/Write/WebFetch/WebSearch/TodoWrite） | 长任务/高危任务实施前一次性方案指导 |
| 固定审阅员.md | new-provider-2/gpt-6.2-sol | enabled（该模型最高档） | Read/Grep/Glob/Bash（只读核查命令） | gpt 路线的包级、收尾和操作预审复核 |
| 继承审阅员.md | inherit | 随主对话 | Read/Grep/Glob/Bash（只读核查命令） | inherit 路线的技术复核 |
| 查询侦察员.md | new-provider-2/deepseek-v4.1-flash | max | Read/Grep/Glob/Bash（只读核查）+ WebSearch/WebFetch | `analyze` 只读侦察，可联网查公开资料 |
| 实施工程师.md | inherit | 随主对话 | Read/Grep/Glob/Edit/Write/Bash（构建自检） | `implement` 定界实施 |
| 测试验证员.md | new-provider-2/deepseek-v4.1-flash | max | Read/Grep/Glob/Bash | `test` 执行验证 |

升级要点（2026-11-24，用户指定）：

1. 思考档位全部开到各模型最高：DeepSeek 对 `max`；gpt-6.2-sol 仅 disabled/enabled 两档，`enabled` 即最高；inherit 档案思考随主对话，不设显式档位。
2. 全员具备 Bash 权限：审阅员和侦察员限只读核查命令，实施工程师限构建/自检命令，方案顾问与测试验证员不受额外限制。
3. 方案顾问开放全部工具（保留"只做方案指导、不改工作包状态、不作复核结论"的角色边界）。
4. 查询侦察员增加 WebSearch/WebFetch 联网查询能力。

说明：

- 上一级的 `*.md` / `*.md.in` 是 `install.sh --with-zcode-agents` 的安装模板，`.md.in` 需显式模型 ID 渲染。**本目录不是 install.sh 的读取来源**，仅作部署参考与恢复基准；模板渲染结果与本快照在工具与思考档位上不同。
- `new-provider-2/...` 是参考机网关的模型 ID；换机部署时按本机 ZCode 可用模型改写 frontmatter 的 `model:` / `thoughtLevel:`，两个 `inherit` 档案无须改动。
