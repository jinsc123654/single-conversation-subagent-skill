# 2.4.3 验收记录

## recommendation 兼容显示标签（2026-11-11）

问题：模型把 `recommendation` 传成中文显示标签（如 `不做测试`），服务端只接受代码值（`none`），返回 `CHOICE_INVALID` 并浪费一轮；问题未创建、状态未改。

修复：`check_selection` 与 `choice_code` 同时接受代码、显示标签和 `code — label` 形式；推荐/提交统一归一化为代码。新增 `test_state_accepts_display_labels_as_codes`。

验证：140 项 pytest 通过。

# 2.4.2 验收记录（历史）

## Codex 任务开始询问、收尾执行（2026-11-11）

变更：Codex 联动问题在任务开始时询问（快速在 mode 后，标准/长任务在 verification 后），只调查/未注册跳过；任务开始只记录选择，收尾再实际执行 codex-review，不重复问。回归范围仍仅标准/长任务。

验证：Skill/AGENTS/README/mcp.md 同步；138 项 pytest 通过。

# 2.4.1 验收记录（历史）

## 回归与 Codex 联动范围（2026-11-11）

变更：`verification` 仅标准/长任务询问，快速/只调查跳过；`codex_review` 在快速/标准/长任务、只有检测到已注册 codex-review 时询问，只调查/未注册跳过；快速不要求 verification，标准/长任务仍要求先确认 verification。唯一推荐仍为不使用；同会话沿用/显式切换不变。

验证：新增 `test_quick_mode_still_asks_registered_codex_review`；138 项 pytest 通过。

# 2.4.0 验收记录（历史）

## Skill 主问、MCP 收集（2026-11-11）

问题：MCP form 15 秒超时只把问题标 `TIMED_OUT`，不写会话状态，后续 verification 认为 mode 未确认；用户打字超过 15 秒被自动推进；弹窗不消失。

决定：Skill 主问，MCP 只收集工作流/状态/证据。ZCode 默认 native AskUserQuestion；MCP form 只作为无原生工具宿主的兜底。

变更：安装器 `zcode→native`；README/Skill/AGENTS 明确 native 主路径；MCP 代码不改；137 项 pytest。

验证：137 项 MCP pytest 通过；真实 ZCode 原生问题流程待新会话；MCP form 15s 不再是 ZCode 主路径。

# 2.3.0 验收记录（历史）

## 多项目根与首轮硬门（2026-11-11）

问题：用户直接新建任务在转换工作台，MCP 固定根是 hc32f460_base，`session_open(project_root=转换工作台)` 报 `PROJECT_MISMATCH`；空参 `session_open` 只能建临时会话，扫描/状态都是别的项目；首轮模型没调用 `session_open` 直接实现；native 的 `interaction_request` 只登记，没真正调用宿主 AskUserQuestion。

修复：`Roots` 把配置根当作允许的工作区边界，`session_open(project_root=<项目>)` 为项目建独立 `<项目>/.scs`；`session_get`/interaction/work/lease/view 都按会话的项目 state；越界返回 `PROJECT_MISMATCH`。Skill/AGENTS：任何首条任务（含转换/脚本执行）必须先四选一，只有明确“不用问/直接做”才跳过；`session_open` 必须传任务项目根；native 的 `NATIVE_QUESTION_REQUIRED` 后必须真正调用 AskUserQuestion，不能只登记。

验证：137 项 pytest 通过；新增 `test_session_open_uses_project_under_workspace`（项目 `.scs`、项目 README 扫描、`session_get`、interaction）与 `test_session_open_rejects_project_outside_workspace`（`PROJECT_MISMATCH`、越界不建 `.scs`）；真实多项目宿主场景待新会话验证。

# 2.2.2 验收记录（历史）

## 同会话沿用与显式切换（2026-11-11）

问题：同一会话重复询问 mode/verification/codex；用户不在电脑前时，MCP form 15 秒自动继续后 ZCode 客户端表单仍留在屏幕上；用户要求同会话默认自动沿用上次选择，只有显式“使用长任务/只调查/快速处理/标准执行”这类切换命令才改变。

修复：Skill/AGENTS 要求 `session_open`/`session_get` 读取已确认的 `mode`/`verification`/`codex_review`，已确认的直接沿用，不再提问；后续验证/审查问题的 `recommendation` 用上次确认值，使 15 秒超时自动继续上次选项。显式切换走 `interaction_reopen(kind="mode", explicit_user_change=true)` → `interaction_request(channel="text", recommendation=[新模式])` → `interaction_submit(action="accept", choices=[新模式])`，不弹表单；普通讨论不推断切换。MCP form 15 秒超时后模型已继续，表单由 ZCode 客户端持有，服务端无法强制关闭；Skill 要求回复时提示用户稍后忽略，不重复提交。native 60s 隐藏 + 5min 可自动关弹窗，但不自动选推荐项。

验证：135 项 MCP pytest 通过；Skill/AGENTS 安装同步；真实 ZCode 会话已观察 MCP form 15 秒后模型按推荐项继续、表单仍显示；未宣称服务端可关闭客户端表单。同会话沿用/显式切换为 Skill 策略，宿主 UI 行为待新会话确认。

# 2.2.1 验收记录（历史）

## 项目扫描与推荐项（2026-11-11）

问题：四选一表单/原生问题只有四个裸选项；模型未扫描工作区、推荐项不在前面、推荐项后没有项目理由；MCP form 的 15 秒只是静态文字，没有可见倒计时。

修复：MCP `session_open` 返回 `workspace_scan`（README/PRODUCT_CLI/applications/components/board/build/dist/顶层结构，只读、无网络、无子进程）与 `mode_options`（模式标签/说明）；`prepare` 自动把扫描放在 `analysis` 前。表单/`display_text` 按推荐项置顶，推荐项标签后附扫描理由，所有选项带模式说明。Skill 原生路径先 `session_open` 取扫描与选项，再 `interaction_request(channel="native")` 取 `display_text`/`question_id`，原生提问推荐项第一位且推荐项 description 带扫描摘要，答复后 `interaction_submit`。ZCode 安装默认从 mcp 改为 native；原生 60s 隐藏 + 5min 是唯一可见倒计时，MCP form 的 15s 是服务端截止、无动画，两者不能兼得。

验证：135 项 MCP pytest 通过；`session_open` 的 `workspace_scan`/`mode_options` 断言通过；真实 RT-Thread 固件项目根扫描输出 README/PRODUCT_CLI/applications（main.c、bang_v2.c）/build/dist；推荐项枚举第一项为 `investigate — 只调查（推荐）：只读梳理/评估，不改代码｜扫描：README…`；`test_p0` 断言推荐项置顶和扫描理由。临时安装器 codex→native、zcode→native、其他→auto。宿主内扫描/推荐项的视觉确认仍需新会话，未宣称桌面 UI 已通过。

# 2.2.0 验收记录（历史）

## ZCode stdio 兼容补充（2026-11-11）

问题：ZCode 的 stdio 客户端先以 2026-07-28 信封探测 `server/discover`，随后按 legacy 形状发 `initialize`/`tools/list`。SDK 双协议循环以首个请求锁定连接 era；探测带来的 modern 锁定会使后续带信封的 legacy 请求被 `INVALID_REQUEST` 拒绝。

修复：`SafeServer.run_stdio_async` 接入 `zcode_compat.zcode_compatible_stream`。ZCode 或未声明身份的探测在本地得到 `-32022`（`supported=["2025-11-25"]`），后续消息剥掉 2026 保留 `_meta` 键；明确标识的非 ZCode 客户端仍走 modern。

验证：新增 3 项过滤层测试；全套 134 项 MCP pytest 通过；真实 stdio 子进程序列 discover→initialize→tools/list 返回 `-32022`、`2025-11-25`、17 工具，stdout 无额外帧、退出码 0。`interaction_ask`/host-channel 10 项测试覆盖 legacy 超时、正常答复、modern fallback submit、legacy 无 form submit、native/text channel submit、跨宿主同 ID 隔离、host/client 歧义隔离、RPC 异常 FAILED 持久化、form 的中文 title/analysis/选项注释/推荐标记/15s/question_id；ZCode 原生自动解析在 `zcode.cjs` 中为 hiddenGrace 60s + autoResolution 300s，不是 15s。未在 ZCode 会话内视觉确认 elicitation 弹窗，不宣称 UI 已通过。

## 宿主通道与 Codex 审查补充（2026-11-11）

Codex 只读审查（`gpt-6.1-sol`，本机二进制 `0.167.0-alpha.3.2`，会话 `01a1c6cc-49e1-7852-aa95-722c5d34c199`）对 851c2e6 的结论是 **REJECT**：Codex 能用原生/文本回落，但当前 MCP-form-first 默认不可验收。P1：modern 回落无 `question_id`、默认路由与“四选一前不调用工具”冲突；P2：`interaction_ask` 异常漏记 FAILED、Codex 精确版本 form 能力证据不足、宿主适配应由安装期选择；P3：legacy 不等于声明 form。Codex 明确：**不需要拆主对话/子 Agent 两套流程**，主对话负责交互，worker 继承 mode/verification。

修复：`session_open` 的 `project_root` 可省略并返回 `question_channel`/`host`；安装器新增 `--host`/`--question-channel`（codex→native、zcode→mcp、其他→auto），示例带 `SCS_HOST`/`SCS_QUESTION_CHANNEL` 和 `host-profile.json`；`prepare` 按 channel 路由（native/text/mcp/auto），fallback 保持 `WAITING_USER` + `question_id`，用户明确回复后直接 `interaction_submit`；modern 无回通道同样保留 `question_id`；`interaction_ask` 与 `interaction_request` 共享 `FAILED/RPC_ERROR` 记账；Skill 四选一前先做一次任务相关只读调查（目标目录/README/git status，不要只列目录），再一次 `session_open`/提问并带 `analysis`；不检查 MCP/配置/项目根，提问后不再调查。worker 不重新 intake。用户反馈 `sess_c9497627-b868-4ae7-aab1-9823cdaebd4b` 中“先四选一、后调查”导致过早提问；本轮已把顺序改回调查优先，同时保留 MCP/配置/项目根排查禁令。

验证：134 项 pytest（9 项 host-channel，另 1 项 form analysis/title；含跨宿主同 ID 隔离、host/client 歧义隔离、RPC 异常 FAILED 持久化）、PyQt live 17 工具、真实 stdio 超时探针；临时安装器 codex→native、zcode→mcp，示例 env 正确。第二轮 Codex 复审 REJECT：默认 client_name 未使用配置 host、README Codex 示例漏 env、README/流程图仍写原生优先；已分别改为 host 前缀命名空间、补 env、按 channel 重画流程。第三轮 Codex REJECT：host+client 拼接可碰撞、流程图缺 form accept/RPC 出口、RPC 测试未断言 FAILED；已改为结构化 SHA-256 命名空间、补图、断言持久化 FAILED。第四轮 Codex REJECT：仅流程图缺 interaction_ask cancel/decline 与 native 不可用→文本；已补图。第五轮 Codex **ACCEPT**（静态复审范围）：同一套实现可支撑 ZCode legacy MCP 15s 与 Codex native/text，无需拆主对话/子 Agent 两套流程；真实宿主 UI 仍待验收。Codex 宿主实测：在 `~/.codex/config.toml` 注册 SCS（`SCS_HOST=codex`、`SCS_QUESTION_CHANNEL=native`）后，`codex exec --approve-for-me` 调用 `diagnostics_check` 成功（OK、17 工具、2.2.0、协议 2025-06-18）；默认 `codex exec` 审批 `never` 会阻止 MCP 工具调用，需交互批准或 `--approve-for-me`。Codex 原生 question UI 的 headless 弹窗 NOT_RUN；未在 Codex 内验证 form/back-channel。

2026-11-11，基线 a1ca6dd，分支 feat/mcp-hybrid。本记录生成于提交前；用户现已授权将 MCP 与可选 PyQt 管理器同仓库提交推送，最终提交与远端分支以 Git 记录为准。用户沿用长任务、主对话直接、不委派、不追加独立审查，并授权更新已安装管理器。

本轮完成：项目栏可折叠并持久记忆、显示真实工作包子项、按最后 MCP 事件/修订或显式打开时间自动排序；读取不制造活动。每8秒可见时更新，排序保留选择、滚动与折叠状态。

Obsidian 首次一键打开改为项目独立 profile。MCP initialize_vault=true 自动准备 `.obsidian` 及 `.scs-obsidian/<库ID>/obsidian.json`；PyQt 点击时也可以独立准备，调用本机 Obsidian 加 --user-data-dir 打开。未修改全局库登记，不伪造 `.obs` 扩展名，不覆盖原有库配置。MCP 本身不启动程序，仍1服务17工具、schema2。

自动验证：121 项 MCP pytest 全部通过；30 项桌面 unittest 全部通过；真实桌面→worker→SDK→stdio 六项集成闭环通过；隔离安装的2.2 wheel新旧协议及SDK1.30客户端通过。证据在 agent/evidence/WP-MCP/R5。深浅色、800×600及全尺寸离屏截图已核对；树显示展开与收起项目。

首次独立 profile 使用本机 Obsidian 1.14.4 创建一次性库，实际观察到 scs-direct-probe 笔记窗口标题。已从安装版真实按钮打开本项目总览，X11窗口标题确认“00_项目总览 - docs - Obsidian 1.15.0”，管理器窗口也存在；全局 obsidian.json 前后SHA256相同。安装版按钮返回值兼容已覆盖布尔/元组两种PyQt形式。未声称所有平台/Obsidian版本已通过。普通全局open_uri仍要求已登记库，桌面按钮采用独立profile消除该限制。未使用远程调试、未关闭沙箱、未更改安全/信任设置。

2.0与2.1历史验收分别见 validation-2.0.md / validation-2.1.md；R4已经按用户要求安装可选管理器，早期“未安装”是当时事实。当前管理器安装不是 MCP 的强制依赖。
