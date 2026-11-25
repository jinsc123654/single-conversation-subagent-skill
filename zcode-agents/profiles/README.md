# 已部署 ZCode Agent 档案快照

本目录收录参考机 `~/.zcode/agents/` 实际部署的 7 个档案（2026-11-25 按角色分离与设备工作流更新），是本 Skill 在 ZCode 宿主上的配套子 Agent 设置：

| 档案 | 模型 | thoughtLevel | 工具 | 职责 |
|---|---|---|---|---|
| 方案顾问.md | new-provider-2/gpt-6.2-sol | enabled（该模型最高档） | Read/Grep/Glob | 长任务/高危任务实施前一次性只读方案指导 |
| 固定审阅员.md | new-provider-2/gpt-6.2-sol | enabled（该模型最高档） | Read/Grep/Glob | gpt 路线的包级、收尾和操作预审复核 |
| 继承审阅员.md | inherit | 随主对话 | Read/Grep/Glob | inherit 路线的技术复核 |
| 查询侦察员.md | new-provider-2/deepseek-v4.1-flash | max | Read/Grep/Glob + WebSearch/WebFetch | `analyze` 只读侦察，可联网查公开资料 |
| 实施工程师.md | inherit | 随主对话 | Read/Grep/Glob/Edit/Write/Bash（构建自检） | `implement` 定界实施 |
| 测试验证员.md | new-provider-2/deepseek-v4.1-flash | max | Read/Grep/Glob/Bash | `test` 固定命令验证 |
| 设备与端到端验证员.md | inherit | 随主对话 | Read/Grep/Glob/Bash（授权 ADB/串口/烧录/浏览器） | `test` + `execution_profile=journey` 现场旅程 |

升级要点（2026-11-25）：

1. 方案顾问/固定审阅员/继承审阅员收回只读，只保留 `Read/Grep/Glob`；需要强模型修复时另开实施 attempt，再新开审阅实例。
2. 查询侦察员保留 `WebFetch/WebSearch`，联网仅限公开资料；不访问内网/私有 URL/凭据。
3. 实施工程师保留 Bash 做编译/测试/lint 自检；测试验证员只跑主对话指定的固定命令。
4. 新增设备与端到端验证员：仅 `Read/Grep/Glob/Bash`，在授权设备/浏览器范围内执行 ADB、串口、烧录或现场旅程，证据写入 attempt 目录；不改源码/配置。
5. 思考档位沿用各模型设置：DeepSeek `max`；gpt-6.2-sol `enabled`；inherit 随主对话。

说明：

- 上一级的 `*.md` / `*.md.in` 是 `install.sh --with-zcode-agents` 的安装模板，`.md.in` 需显式模型 ID 渲染。本目录是部署参考与恢复基准，不是 install.sh 的读取来源；模板渲染结果与快照应保持工具/档位一致。
- `new-provider-2/...` 是参考机网关的模型 ID；换机部署时按本机 ZCode 可用模型改写 frontmatter 的 `model:` / `thoughtLevel:`，两个 `inherit` 档案无须改动。
- 权限校验：`python scripts/check_agent_permissions.py` 以 `zcode-agents/permissions.json` 比对已部署档案；超出矩阵的角色停止派发。
