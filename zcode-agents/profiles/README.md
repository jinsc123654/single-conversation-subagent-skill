# 已部署 ZCode Agent 档案快照

本目录收录从参考机 `~/.zcode/agents/` 抓取的 6 个实际部署档案（快照日期 2026-11-24），是本 Skill 在 ZCode 宿主上的配套子 Agent 设置：

| 档案 | 模型 | thoughtLevel | 职责 |
|---|---|---|---|
| 方案顾问.md | new-provider-2/gpt-6.2-sol | enabled | 长任务/高危任务实施前一次性只读方案指导 |
| 固定审阅员.md | new-provider-2/gpt-6.2-sol | enabled | gpt 路线的包级、收尾和操作预审只读复核 |
| 继承审阅员.md | inherit | — | inherit 路线的只读技术复核 |
| 查询侦察员.md | new-provider-2/deepseek-v4.1-flash | low | `analyze` 只读侦察 |
| 实施工程师.md | inherit | — | `implement` 定界实施 |
| 测试验证员.md | new-provider-2/deepseek-v4.1-flash | low | `test` 执行验证 |

说明：

- 上一级的 `*.md` / `*.md.in` 是 `install.sh --with-zcode-agents` 的安装模板，`.md.in` 需显式模型 ID 渲染。**本目录不是 install.sh 的读取来源**，仅作部署参考与恢复基准。
- 方案顾问、固定审阅员在部署后做过手工调整（派发措辞、description），与当前模板渲染结果不完全一致，以本快照为准。
- `new-provider-2/...` 是参考机网关的模型 ID；换机部署时按本机 ZCode 可用模型改写 frontmatter 的 `model:` / `thoughtLevel:`，两个 `inherit` 档案无须改动。
