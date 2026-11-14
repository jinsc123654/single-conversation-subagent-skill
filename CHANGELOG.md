# Changelog

## 2.5.0 — 2026-11-14

- 长任务语义收紧：所有任务性查找、分析、实现、测试都派执行子 Agent，主对话不直接执行；主对话只保留目标、决策、未决问题、下一步、工作包修订、证据路径与协调记录，验收只核对关键证据。
- Skill/协议/AGENTS 默认规则/README/MCP 说明同步；快速、标准、只调查行为不变。

## 2.4.4 — 2026-11-14

- 仓库纳入 `codex-review/` 技能：Codex 只读独立审查（固定 `gpt-6.1-sol`），含 brief 模板与「会话复用」规则——审查基线（项目 + WP/Rn + 审查对象 + 只读清单 + 验收标准）一致才续跑，基线变化或审查期间有写入则新开；续跑显式传 `-m` 并核验启动头。
- `install.sh` 新增 `--skill codex-review`：纯 Skill 安装到 `$AGENT_HOME/skills/codex-review/`，不注入 AGENTS 规则、不建 MCP runtime；`--check`/`--uninstall` 复用同一路径。主技能安装路径与行为不变。
- README 增加双技能说明；会话复用规则按 Codex 设计审查（首轮 REJECT）收敛后落盘。

## 2.4.3 — 2026-11-11

- `recommendation` 和提交 choices 同时接受代码值（`none`）与中文显示标签（`不做测试`/`本轮冒烟`/`不使用`）；避免模型按界面文案传参时 `CHOICE_INVALID`。
- 新增 `test_state_accepts_display_labels_as_codes`；140 项 pytest。

## 2.4.2 — 2026-11-11

- Codex 联动提问时机改为任务开始时：快速/标准/长任务、检测到已注册 codex-review 时询问是否收尾执行；标准/长任务在 verification 后，快速在 mode 后；只调查/未注册跳过。
- 任务开始只记录选择，收尾再实际执行 codex-review，不重复问；唯一推荐仍为不使用。
- 138 项 pytest；Skill/AGENTS/README/mcp.md 同步。

## 2.4.1 — 2026-11-11

- 回归范围：仅标准/长任务询问；快速/只调查跳过。
- Codex 联动：快速/标准/长任务都询问（检测到已注册 codex-review；只调查/未注册跳过），唯一推荐不使用；快速不要求验证范围。
- 同会话沿用/显式切换不变；138 项 pytest。

## 2.4.0 — 2026-11-11

- 架构收口：Skill 主问；ZCode 默认 native AskUserQuestion；MCP 只做 `session_open`/选择记录/`interaction_submit`/工作包/证据/视图/handoff，不默认驱动 MCP form。
- MCP form/15s `interaction_ask` 保留给没有原生提问工具的宿主；ZCode 不再使用。原生 60s 隐藏 + 5min；打字超过 15 秒不会被 MCP 截断；首问超时也不影响后续（由 Skill 继续）。
- 安装器 ZCode 默认 native；README/Skill/AGENTS 同步。
- 137 项 pytest（MCP 代码不变；默认通道/文档更新）。

## 2.3.0 — 2026-11-11

- MCP 多项目根：配置根是允许的工作区边界；`session_open(project_root=<项目>)` 为项目建独立 `<项目>/.scs`，`session_get`/interaction/work/lease/view 都按会话的项目 state。修复直接新建任务在别的项目时 `PROJECT_MISMATCH`、空参临时会话扫描错项目的问题。
- Skill 首条任务硬门：任何首条任务（含转换/脚本执行）必须先四选一；只有明确“不用问/直接做”才跳过；不得因“转换下”直接实现。`session_open` 必须传任务项目根；native 的 `NATIVE_QUESTION_REQUIRED` 后必须真正调用 AskUserQuestion，不能只登记。
- 137 项 pytest；新增工作区内项目 `.scs`/`workspace_scan`/`session_get`/interaction 和越界 `PROJECT_MISMATCH` 测试。

## 2.2.2 — 2026-11-11

- 同会话沿用已确认的 `mode`/`verification`/`codex_review`，不再重复提问；后续验证/审查问题的 `recommendation` 使用上次确认值，MCP form 15 秒超时后自动继续选中上次选项。
- 只有用户显式说“使用长任务/只调查/快速处理/标准执行”才重开 mode；显式切换走 `interaction_reopen` → `interaction_request(channel="text")` → `interaction_submit`，不弹表单；普通讨论不推断切换。
- `mcp` 15 秒超时后模型继续，ZCode 的 MCP form 弹窗可能仍留在屏幕上；Skill 要求提示用户稍后忽略，不重复提交。弹窗自动关闭只能 native（60s 隐藏 + 5min），二者不能兼得。ZCode 安装默认改为 `mcp`（自动继续），`--question-channel native` 保留可见倒计时。
- 135 项 pytest 通过（MCP 代码未改动，Skill/AGENTS 同步安装）。

## 2.2.1 — 2026-11-11

- `session_open` 新增 `workspace_scan`（只读项目扫描：README/PRODUCT_CLI/关键目录/build/dist/顶层结构）与 `mode_options`（模式标签/说明）；`interaction_request`/`interaction_ask` 的 `analysis` 自动前置扫描结果。
- MCP form 推荐项置顶；推荐项标签后直接附扫描理由；所有选项带模式说明；`describe`/`display_text` 同步。选项值仍兼容代码值/短标签。
- Skill 原生路径：`session_open` 取扫描/选项 → `interaction_request(channel="native")` → 原生 AskUserQuestion（推荐项第一位，推荐项 description 带扫描摘要，其他选项带模式说明）→ `interaction_submit`。ZCode 安装默认改为 native；原生 60s 隐藏 + 5min 是唯一可见倒计时。`--question-channel mcp` 保留 15s 服务端截止表单，但无动画倒计时；两种目标不能兼得。
- 全套 135 项 pytest；真实 RT-Thread 固件根扫描、推荐项枚举顺序与扫描理由断言通过。

## 2.2.0 — 2026-11-11

- PyQt 项目栏改为可展开/收起的项目与工作包树，记住折叠状态，按最后 MCP 活动或显式打开时间自动排序。
- Obsidian 首次一键打开：本地 `.obsidian` 与项目独立 profile/库映射自动准备，使用 `--user-data-dir` 启动，不修改全局库列表、不关闭用户其他库。MCP 初始化同步生成 profile；工具数量增至17（interaction_ask）。
- 新增 `interaction_ask`：MCP form + 服务端 `timeout_seconds`（默认 15s），仅 legacy back-channel；超时标记 `TIMED_OUT` 并返回推荐项，不写 `CONFIRMED`。modern/无 form fallback 保持 `WAITING_USER` 和 `question_id`，用户明确回复后直接 `interaction_submit`；RPC 异常统一记 `FAILED/RPC_ERROR`。`interaction_ask`/`interaction_request` 新增 `analysis`（1-2 句推荐理由），MCP form 显示中文 title、分析、选项注释（如 `quick — 快速处理`）、推荐标记、15s 截止和 question_id；选项值带注释，代码值仍兼容。ZCode 原生 AskUserQuestion 自动解析为 60s 隐藏 + 5min 截止，不是 15s。
- 宿主适配：安装器新增 `--host codex|zcode|claude|generic` 与 `--question-channel auto|native|mcp|text`，默认 codex→native、zcode→mcp、其他→auto；写入 `SCS_HOST`/`SCS_QUESTION_CHANNEL` 和 `host-profile.json`。`session_open` 返回 channel，Skill 按 native/text/mcp/auto 路由；四选一前先完成一次任务相关只读调查（目标目录/README/git status，不要只列目录），再一次 `session_open`/提问并带 `analysis`；不检查 MCP 注册/配置/项目根，提问后不再调查。执行子 Agent 继承 mode/verification，不重新提问。`session_open` 把配置 host 与 client_name 做结构化 SHA-256 命名空间（`h<sha256>`，返回 `client_label`），跨宿主同 conversation ID 不共用会话；补跨宿主、host/client 歧义与 RPC FAILED 测试。
- Skill 顺序修正：避免“先四选一、后调查”导致的过早提问和空泛推荐；恢复任务相关只读调查优先，同时保留 MCP/配置/项目根排查禁令，提问后等待、不再继续调查。
- 当前 Linux / Obsidian 1.14.4 完成真实首次笔记窗口验证。新配置冲突保留原内容；已有库设置保持原样。
- ZCode stdio 兼容：识别 ZCode 的 2026-07-28 `server/discover` 探测与后续 legacy 请求混用；本地回绝探测并剥离保留 `_meta` 信封，回落 2025-11-25 握手，明确标识的非 ZCode 客户端仍走 modern。全套 134 项 pytest、9 项 host-channel 测试、真实 stdio 序列与 PyQt live 17 工具通过。

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
