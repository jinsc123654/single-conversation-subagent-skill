# 已部署 ZCode Agent 档案快照

本目录收录 7 个 ZCode 子 Agent 的参考部署档案，以参考机 `~/.zcode/agents/` 的模型和工具配置为基准。当前 ZCode GUI 的 Agent 名称要求 3–50 个 ASCII 字母、数字或连字符，因此文件名和 frontmatter 的 `name` 使用下表中的 ID；中文角色名保留在说明与协作叙述中。

| 中文角色（旧文件名去掉 `.md`） | 档案 ID（新文件名加 `.md`） | 模型 | thoughtLevel | 工具 | 职责 |
|---|---|---|---|---|---|
| 方案顾问 | `scs-advisor` | new-provider-2/gpt-6.2-sol | enabled（该模型最高档） | Read/Grep/Glob | 长任务/高危任务实施前一次性只读方案指导 |
| 固定审阅员 | `scs-fixed-reviewer` | new-provider-2/gpt-6.2-sol | enabled（该模型最高档） | Read/Grep/Glob | gpt 路线的包级、收尾和操作预审复核 |
| 继承审阅员 | `scs-inherited-reviewer` | inherit | 随主对话 | Read/Grep/Glob | inherit 路线的技术复核 |
| 查询侦察员 | `scs-scout` | new-provider-2/deepseek-v4.1-flash | max | Read/Grep/Glob + WebSearch/WebFetch | `analyze` 只读侦察，可联网查公开资料 |
| 实施工程师 | `scs-implementer` | inherit | 随主对话 | Read/Grep/Glob/Edit/Write/Bash + WebFetch/WebSearch/TodoWrite | `implement` 定界实施、内部清单、自检与公开规格核对 |
| 测试验证员 | `scs-test-verifier` | new-provider-2/deepseek-v4.1-flash | max | Read/Grep/Glob/Bash | `test` 固定命令验证 |
| 设备与端到端验证员 | `scs-device-verifier` | new-provider-2/deepseek-v4.1-flash | max | Read/Grep/Glob/Bash（授权 ADB/串口/烧录/浏览器） | `test` + `execution_profile=journey` 现场旅程 |

## 从中文文件名迁移

使用 `install.sh --host zcode --with-zcode-agents` 时，若目标目录（默认 `~/.zcode/agents`）仍有上表任何一个旧中文文件名，安装器会列出迁移对应关系并在写入前退出。它不会自动迁移或覆盖档案。若只更新 Skill，可以不传 `--with-zcode-agents`，但这不会迁移档案。

1. 在 Agent 目录之外备份原档案；用现有档案迁移，不要用本目录的快照替换本机配置。
2. 按表把旧文件改名为 `<新 ID>.md`，把 frontmatter 中的 `name` 改为对应 ID。保留 `model`、`thoughtLevel`、`tools`、`description` 和提示词。若新文件名已存在，先对比两份，再手动保留或合并所需配置，勿覆盖任一原件；把不再使用的旧档案移出 Agent 目录，不要同时保留两个活动档案。
3. 迁移后重跑安装器（如仍需补齐缺失档案），然后新开对话，在 ZCode 中确认新 ID、模型和工具。安装器保留已有新 ID 档案；仅在提供相应模型 ID 时添加缺失的固定模型档案。

## 角色与权限（2026-11-25 升级要点）

1. 方案顾问/固定审阅员/继承审阅员收回只读，只保留 `Read/Grep/Glob`；需要强模型修复时另开实施 attempt，再新开审阅实例。
2. 查询侦察员保留 `WebFetch/WebSearch`，联网仅限公开资料；不访问内网/私有 URL/凭据。
3. 实施工程师保留 Bash 做编译/测试/lint 自检；测试验证员只跑主对话指定的固定命令。
4. 新增设备与端到端验证员：仅 `Read/Grep/Glob/Bash`，在授权设备/浏览器范围内执行 ADB、串口、烧录或现场旅程，证据写入 attempt 目录；不改源码/配置。
5. 思考档位沿用各模型设置：DeepSeek `max`；gpt-6.2-sol `enabled`；inherit 随主对话。

说明：

- 上一级的 `*.md` / `*.md.in` 是 `install.sh --with-zcode-agents` 的安装模板，`.md.in` 需显式模型 ID 渲染。本目录是部署参考与恢复基准，不是 install.sh 的读取来源；模板渲染结果与快照应保持工具/档位一致。
- `new-provider-2/...` 是参考机网关的模型 ID；换机部署时按本机 ZCode 可用模型改写 frontmatter 的 `model:` / `thoughtLevel:`，两个 `inherit` 档案无须改动。
- 权限校验：`python scripts/check_agent_permissions.py` 以 `zcode-agents/permissions.json` 比对已部署档案；超出矩阵的角色停止派发。
