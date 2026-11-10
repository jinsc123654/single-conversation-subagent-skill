# Changelog

## 2.2.0 — 2026-11-11

- PyQt 项目栏改为可展开/收起的项目与工作包树，记住折叠状态，按最后 MCP 活动或显式打开时间自动排序。
- Obsidian 首次一键打开：本地 `.obsidian` 与项目独立 profile/库映射自动准备，使用 `--user-data-dir` 启动，不修改全局库列表、不关闭用户其他库。MCP 初始化同步生成 profile；工具数量增至17（interaction_ask）。
- 新增 `interaction_ask`：MCP form + 服务端 `timeout_seconds`（默认 15s），仅 legacy back-channel；超时标记 `TIMED_OUT` 并返回推荐项，不写 `CONFIRMED`。modern 协议明确返回 `MODERN_NO_BACKCHANNEL` 文本回退。Skill 四选一前不再调查项目根/MCP 注册；ZCode 原生 AskUserQuestion 自动解析为 60s 隐藏 + 5min 截止，不是 15s。
- 当前 Linux / Obsidian 1.14.4 完成真实首次笔记窗口验证。新配置冲突保留原内容；已有库设置保持原样。
- ZCode stdio 兼容：识别 ZCode 的 2026-07-28 `server/discover` 探测与后续 legacy 请求混用；本地回绝探测并剥离保留 `_meta` 信封，回落 2025-11-25 握手，明确标识的非 ZCode 客户端仍走 modern。新增过滤层 3 项测试；全套 124 项 pytest 与真实 stdio 序列通过。

## 2.1.0 — 2026-11-10

- 新增可选 PyQt5 桌面管理器：项目/目录/工作包/记录查看、Obsidian 打开、短期 stdio 自检、受管视图刷新及可恢复归档。独立依赖，不加入 MCP 安装；不控制宿主进程。

- 同一 MCP 服务增加 project_view_refresh / project_view_info，共 16 工具；每个项目 docs-root 可作为独立 Obsidian 库，无需插件。
- 总览 → 状态分组 → 工作包/修订/证据的 Markdown 视图；已报告通过的子任务与工作包待验收分开，证据变化进入需要关注。
- 手工批注区原样保留、管理清单与内容检查、原子文件替换、生成段冲突保护和中断恢复；不覆盖已有 Obsidian 配置，不写全局库注册或启动应用。
- 已启用视图的 MCP 工作包变更自动刷新，可显式关闭。工作包保存与视图失败分别报告，防止重复提交。
- SQLite schema 1 → 2 迁移保留既有数据并保存真实修订快照；只回填旧库实际当前修订，不推断缺失历史。2.0 程序无法直接读取 schema 2，回滚需恢复升级前状态备份。
- 返回经编码的 Obsidian 打开链接和首次注册说明。新增视图、冲突、迁移、并发、URI 和协议测试；实测 GUI 限制见 docs/validation.md。

## 2.0.0 — 2026-11-10

- 新增 Python 3.11+ 官方 MCP SDK 2.3.0、本地 SQLite、stdio 服务。Skill 管策略、MCP 管交互/确定性状态与证据、原生 Subagent 执行；主对话协调和验收保持不变。
- 原生提问优先，其次声明支持的 MCP 表单，最后完整文本回退并结束当轮等待。Resolver/Elicit 跨现代多轮及 legacy 协议。
- **不兼容变更**：移除旧版十五秒命令等待后自动选择推荐项的机制；未回复、取消、拒绝和 RPC 故障不再采用默认值。本版只记录可观测超时，不自动确认。
- 对话身份显式绑定；未知 ID 临时会话；问题唯一 ID、幂等提交、迟到隔离、脱敏事件、UI 不可观测分类。
- 工作包版本/修订、范围检查、证据验证、资源租约和显式 handoff；工作包可续接，会话选择不复制。
- 保留 docs-root 优先顺序、所有既有 Markdown/证据和报告路径；服务不覆盖用户编辑，纯 Skill 恢复需显式核对。
- 安装增加 skill/hybrid/check，兼容旧参数，生成配置示例，备份且不改写其他客户端配置；Python/MCP 不可用时纯 Skill 仍可工作。
- 新增新旧协议、stdio、故障、并发、路径、证据、安装与恢复测试；实测限制详见 docs/validation.md。

## 1.3.4 及以前

历史版本提供纯 Skill 两级协作、每对话首次模式/验证/审查询问、动态推荐、docs-root 和证据约束。旧版超时策略已被 2.0 明确撤回，旧发行说明与示例不能用作当前执行规则。原提交历史保留完整变更记录。
