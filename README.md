# single-conversation-subagent skill 1.3.3

记录日期：2026-10-19（1.0.x）；更新日期：2026-11-02（1.3.3；1.3.2/1.3.1 同日）

把“单对话 subagent：两级协作协议”打包成用户级 skill。安装后，新的会话在每个新任务开始时先四选一，再按选中的模式执行：

- **主对话（第一级 / 协调层）**：发现问题、分析需求、制定顶层目标、授权范围和最终验收；同时直接维护阶段记忆、拆分子任务、创建和调度执行子 Agent、汇总结果。第一级协调不再交给另一个子 Agent。
- **执行子 Agent（第二级）**：名称由主对话按任务起（如“支付模块分析子 Agent”），模型和类型由当前会话环境决定；负责分析、实现或测试，返回证据，不再创建下一级 Agent。

不使用外部桥接式分发。

## 架构视图

完整流程、四选一、验证范围、记忆布局和回传格式见 [docs/单对话subagent-两级协作协议.md](docs/单对话subagent-两级协作协议.md)。

架构图在源码仓库的 `docs/archify/`（HTML/GIF）；发行包为控制体积不包含这些资源。需要查看时在源码仓库打开 `docs/archify/skill-architecture.html`。

每个新的用户任务开始时先四选一：**快速处理 / 标准执行 / 长任务 / 只调查**，包括“推送吧”“改一下”“跑一下”这类直接命令；同一任务的追问不重复问，只有明确说“不用问，直接做”时才可跳过。**不得按任务大小/类型（纯问答、小改、单文件、快速查询等）自动跳过；任务大小只影响推荐项，不决定是否提问。** 推荐项由 agent 在必要只读调查后按范围/风险/成本决定，不固定同一项；选快速处理或只调查后，不再做验证范围多选。

四选一必须带推荐项并默认勾选；先做必要的只读调查，再由 agent 按范围、风险、改动面、验证成本选择，不固定同一项；拿不准可推荐标准执行。推荐项只决定默认选中，不代表可以跳过提问。

未选择或超时不得提前落默认值：只有用户明确不选或 15 秒超时（客户端倒计时或 `sleep 15`）时，才按推荐项继续，不重复追问；默认用普通文本提问并 `sleep 15` 做有界等待，仅明确有倒计时才用交互提问，不用会无限阻塞的 AskUserQuestion 等工具；仍未回复就继续。

默认记忆布局跟随 docs-root：docs-root 取首个存在的 `00_doc` / `00_docs` / `docs` / `doc`；均无则新建 `docs`。

```markdown
## 记忆文件
- 项目记录：<docs-root>/agent/AGENT_PROJECT.md
- 协作记录：<docs-root>/agent/AGENT_COORDINATION.md
- 证据目录：<docs-root>/agent/evidence/
```

技能生成的正式文档（设计、方案、说明、总结、教程、会议纪要等）默认按以下顺序落盘：

| 顺序 | 目录 | 条件 |
| --- | --- | --- |
| 1 | `<项目根>/00_doc/` | 存在就用 |
| 2 | `<项目根>/00_docs/` | 存在就用 |
| 3 | `<项目根>/docs/` | 存在就用 |
| 4 | `<项目根>/doc/` | 存在就用 |
| 5 | `<项目根>/docs/` | 都不存在时新建 |

记忆、WP 审查报告、证据保持既有职责：记忆/证据在 `<docs-root>/agent/`，WP 审查报告在 `04_test/report/`；目录规则只决定新文档落点，不自动迁移既有文件。

标准执行和长任务在同一对话内每个新任务开始前先做一次验证范围多选：操作、时机（本轮/收尾）、目的、预计耗时、资源、风险、说明。只给一个推荐路径：由 agent 先做必要的只读调查，再按改动风险、影响面、验证成本、资源和回滚难度选择，不固定为“快速冒烟+定向回归”；其他选项不单独标推荐/默认。定向/全量回归和稳定性放在收尾执行，不每轮重复；未选中的操作不进入计划；烧录/擦除等破坏性操作单独确认。

“不做测试”和“不做测试，记录测试方法”二选一，且与测试操作互斥；前者标记 `NOT_RUN`，后者只记录方法，不标 `PASS`。

四选一和验证范围每次只能有一个推荐路径，不得出现多个“推荐/默认”。只有在明确不选或 15 秒超时（客户端倒计时或 sleep 15）时才按推荐/默认继续，等待期间不落默认值，不重复追问；默认用普通文本提问并 sleep 15，仅明确有倒计时才用交互提问，不用会无限阻塞的 AskUserQuestion 等工具。

本地装了 `codex-review` 技能（联动技能）时，标准执行/长任务在验证范围问完后紧接着追加一问：**收尾是否用 Codex 独立审查？** 只有“不使用（默认）”与“使用：收尾按 codex-review 技能做只读独立审查”两个选项，默认与超时均为“不使用”，不重复追问。注册状态分三态：确定已装才问，确定未装跳过，未知按未装处理（可查 `~/.agents/skills/codex-review/SKILL.md` 或项目 `.zcode/skills/codex-review/SKILL.md` 辅助）。用户已明确要求 Codex 审查时不再问此问，直接执行；同一工作包/修订只审一次。

## 设计初衷与上下文预算

长任务模式的核心是隔离长执行线：主对话只保留目标、决策、未决问题、下一步和证据路径；执行细节下沉到子 Agent 和 `<docs-root>/agent/evidence/`。它不是缓存清理机制，主对话仍会随轮次增长。

- 三级记忆：主对话 → `AGENT_COORDINATION.md` → 证据文件。
- 子 Agent 最终回传固定字段，不贴原始日志（建议 ≤ 1–2 KB）。
- 每个工作包/阶段结束写 handoff；主对话接近上下文上限时新开会话恢复。
- 长测试后台执行，返回 PID/日志/停止方式；不长期占用子 Agent。
- 子 Agent 上下文过大时停止并新建，传入摘要和证据路径。

## 版本变化

### 1.3.3

- 四选一和验证范围的推荐项改为 agent 在必要只读调查后按范围/风险/改动面/验证成本选择，不再固定“标准执行”或“快速冒烟+定向回归”；仍只标一个推荐并默认勾选，推荐不免问。

### 1.3.2

- 有界等待：默认用普通文本提问并执行 `sleep 15`；仅明确有倒计时才用交互提问，不使用会无限阻塞的 `AskUserQuestion` 等工具；仍未回复就按推荐/默认继续，不无限等待。
- SKILL、AGENTS 片段、protocol、长文档、README 同步“客户端倒计时或 sleep 15”的超时语义。

### 1.3.1

- 上下文压缩：SKILL.md 1,514 → 1,154 字符（-23.8%）；常驻 AGENTS 标记块 985 → 700 字符（-28.9%）；description 110 → 59 字符。
- protocol 不压缩、保持按需加载；六项行为基线（四选一、验证范围、Codex 联动、超时、docs-root/agent、子 Agent 边界）保留。
- 同步 `~/.codex/skills` 的旧副本（原先仍固定 docs/agent、无 1.3.0 边界）。

### 1.3.0

- 记忆根改为 **docs-root/agent**：docs-root 取首个存在的 `00_doc` / `00_docs` / `docs` / `doc`；均无则新建 `docs`。`AGENT_PROJECT.md`、`AGENT_COORDINATION.md`、`evidence/` 都在 docs-root/agent/，不再固定 `docs/agent`。
- Obsidian vault 根 = docs-root；只读视图在 docs-root/agent/acceptance/。esp32_epd 已迁移到 `00_doc/agent`，vault 根 `00_doc`。
- 同步 SKILL、protocol、AGENTS 片段、README、长文档；`04_test/report` 仍是 WP 审查报告路径。

### 1.2.1

- 压缩入口 `SKILL.md`：8,476 → 3,147 字节（1,393 字符，-62.9%），六项行为基线不变；模板/字段/异常细节继续按需读 `references/protocol.md`。
- 明确 Codex 外部审查边界：非子 Agent、只能由主对话启动、子 Agent 不得启动。
- 文档输出例外前置；明确目录规则只决定新文档落点，不自动迁移既有文件。
- 修正 README 的超时旧表述、验证示例和字符数记录；长文档流程图补齐“明确要求审查/未知注册/不用问直接做”分支。
- 移除默认文件名与 `_NATIVE` 隐式改名的冲突：共存需显式授权。

### 1.2.0

- 增加正式文档输出位置规则：`00_doc/` → `docs/` → `doc/` → 新建 `docs/`；协议记忆、审查报告、证据保持既有路径。
- 增加与 `codex-review` 技能的联动：本地装了 codex-review 时，标准执行/长任务在验证范围问完后追加一问“收尾是否用 Codex 独立审查”；默认与 15 秒超时均为“不使用”，不重复追问。
- 本地未装 codex-review 时自动跳过该问，不影响原有四选一 + 验证范围两问流程；联动问只出现在标准执行/长任务，快速处理/只调查只有四选一。SKILL.md、references/protocol.md、AGENTS.snippet.md 三处同步更新。
- 四选一超时语义修正（2026-10-28 用户定）：15 秒未选择按推荐项继续（此前固定按“标准执行”）；验证范围超时=唯一推荐路径，联动问超时=不使用，三问统一为“超时=推荐/默认”。
- 按 Codex 独立审查（REJECT）修正：超时只在“明确不选或客户端报告超时”时生效，等待期间不落默认值；只调查可派只读分析子 Agent（与“不建执行子 Agent”的矛盾消除）；联动问改为注册状态三态判断，明确要求审查时直接执行，同工作包/修订只审一次，迟到回复绑定问题与修订；设计文档 mermaid 补 Codex 审查分支。
- 上下文瘦身（2026-10-28 记录，后续 1.2.1 再压缩）：当时 SKILL.md 为 8,476 字节；1.2.1 压到约 3.0 KB，`references/protocol.md` 保持按需加载、未压缩。
- 设计记录 docs/单对话subagent-两级协作协议.md 与架构视图（HTML/GIF/PNG）同步重生：新增 codex-review 联动节点与“装了才追问”连线。

### 1.1.0

- 四选一不再有任何自动跳过：每个新用户任务都必须发起提问，不得按任务大小/类型（纯问答、小改、单文件、快速查询等）自行判断跳过。
- 唯一例外是用户明确说“不用问，直接做”；此时按快速处理开始，若实际需要标准/长任务仍问验证范围。
- 推荐项只决定默认勾选，不再被解释为“可以跳过提问”。
- 同步用户级 10-22 增加的子 Agent 模型继承说明（默认继承主对话 provider/model，不写死）。

### 1.0.2

- 第一级协调由主对话直接承担：阶段记忆、拆分、创建和调度子 Agent、汇总都在主对话中进行，不再创建“协调子 Agent”。
- 执行子 Agent 只负责分析/实现/测试，按任务命名，例如“支付模块分析子 Agent”“回归测试子 Agent”。
- `AGENT_COORDINATION.md` 改由主对话维护；上下文压缩或重启后由主对话从该文件恢复。
- 默认记忆文件改为 `<项目根>/docs/agent/AGENT_PROJECT.md`、`docs/agent/AGENT_COORDINATION.md`；证据目录改为 `docs/agent/evidence/`。
- 任务类型选择恢复为每个新的用户任务一次；同一任务的追问不重复问，直接命令不能跳过。快速处理/只调查不启执行子 Agent，也不做验证范围多选。
- 四选一增加推荐项/默认勾选：普通工程默认标准执行，拿不准也默认标准执行，用户直接确认即可。
- 四选一未选择/超时默认标准执行，不按推荐项，不重复追问；当时以为倒计时由客户端实现（1.3.2 起改为默认普通文本提问 + `sleep 15`）。
- 验证范围未选择/超时按唯一推荐路径（快速冒烟 + 收尾定向回归）继续，不重复追问。
- 标准执行和长任务在同一对话内每个新任务开始前增加验证范围多选；只给一个推荐路径（快速冒烟 + 收尾定向回归），其他选项不标推荐；定向/全量回归和稳定性收尾执行，不每轮重复；增加“不做测试”和“不做测试，记录测试方法”两个互斥选项。
- 增加设计初衷与上下文预算/补救措施：有界回传、阶段 handoff、阈值新会话、长测试后台化、子 Agent 膨胀处理。
- 分发包和默认规则同步更新。

### 1.0.1

- 角色名统一为“协助 Agent”，由主对话按任务命名，不再固定使用模型名或其他固定称呼。
- 移除所有厂商和实现名词，统一使用“主对话、协助 Agent、执行子 Agent、会话环境、原生 Agent 工具、桥接模式”。
- 记忆文件改为 `AGENT_PROJECT.md` / `AGENT_COORDINATION.md`；证据目录改为 `.agent/evidence/`（1.0.2 起默认改为 `docs/agent/evidence/`）。
- 安装器改用 `AGENT_HOME`（默认 `~/.agents`），并支持 `--instructions-file` 指定用户级指令文件。

### 1.0.0

- 首次打包：skill、完整协议、安装/卸载脚本、默认规则片段。

## 包内容

```text
single-conversation-subagent-skill/
├── README.md
├── VERSION
├── install.sh                  # 用户级安装/卸载脚本
├── AGENTS.snippet.md           # 默认规则片段（带幂等标记）
├── single-conversation-subagent/
│   ├── SKILL.md                # skill 本体：触发描述、流程、边界、子 Agent 模板
│   └── references/
│       └── protocol.md         # 完整两级协作协议
└── docs/
    └── 单对话subagent-两级协作协议.md   # 设计记录
```

## 要求

- 会话环境支持原生 `Agent` 工具和 `SendMessage`。
- Linux/macOS 使用 bash 安装脚本；Windows 可按“手动安装”复制文件。
- 脚本只写用户目录，不需要 `sudo`。

## 安装

### 从 GitHub 仓库安装

```bash
git clone https://github.com/jinsc123654/single-conversation-subagent-skill.git
cd single-conversation-subagent-skill
bash install.sh --with-default-rule
```

### 从发布包安装

解压后进入包目录：

```bash
tar -xzf single-conversation-subagent-skill-1.3.3.tar.gz
cd single-conversation-subagent-skill-1.3.3
```

### 推荐：安装 skill 并设为默认规则

```bash
bash install.sh --with-default-rule
```

会把 skill 安装到 `$AGENT_HOME/skills/single-conversation-subagent/`，并把带标记的默认规则追加到 `$AGENT_HOME/AGENTS.md`。`AGENT_HOME` 默认是 `~/.agents`。

### 指定用户级指令文件

如果当前会话环境读取的指令文件不在 `$AGENT_HOME/AGENTS.md`，直接指定它：

```bash
bash install.sh --instructions-file /path/to/AGENTS.md
```

### 只安装 skill

```bash
bash install.sh
```

只复制 skill，不修改指令文件。此时 skill 会在相关任务中按描述触发，但不会强制所有对话默认加载。

### 自定义 agent 配置目录

```bash
AGENT_HOME=/path/to/agent-home bash install.sh --with-default-rule
```

`AGENT_HOME` 应指向包含 `skills/` 和 `AGENTS.md` 的用户级目录，默认是 `~/.agents`。

## 安装脚本行为

- 安装前如果目标 skill 目录已存在，会先复制备份到 `*.bak.<时间戳>.<pid>`，再替换。
- `--with-default-rule` 修改 `$AGENT_HOME/AGENTS.md` 前会备份原文件；如果已有 `<!-- single-conversation-subagent begin -->` 标记，则不重复追加。
- `--instructions-file <path>` 把规则写入指定文件，并隐含 `--with-default-rule`。
- `--uninstall` 会备份并删除已安装的 skill 目录，并移除指令文件中带标记的整段规则。若当初用了自定义指令文件，卸载时也要传同一个 `--instructions-file`。
- 拒绝 `AGENT_HOME=/` 和不符合 `<AGENT_HOME>/skills/single-conversation-subagent` 的删除目标，避免误删。
- 不修改工作区文件、不写系统目录、不执行网络操作。

## 验证

1. 重开一个新的会话，让 skill 注册表和用户级指令文件重新加载。
2. 检查文件存在：

   ```bash
   ls "$HOME/.agents/skills/single-conversation-subagent/SKILL.md"
   grep -n 'single-conversation-subagent' "$HOME/.agents/AGENTS.md"
   ```

   若使用了自定义 `AGENT_HOME` 或 `--instructions-file`，把路径换成实际位置。

3. 在会话里给一个跨文件任务，例如“调查这个 bug 的根因并给出修复与测试”，模型应先完成四选一；选标准执行/长任务后继续问验证范围（本地装了 codex-review 时再追加收尾审查问），再由主对话定义工作包、维护阶段记忆、拆分并创建执行子 Agent。
4. 如果客户端支持斜杠命令，也可显式调用 `/single-conversation-subagent <任务>`。

## 手动安装（Windows / 无 bash）

1. 把 `single-conversation-subagent/` 整个目录复制到：
   `<AGENT_HOME>/skills/single-conversation-subagent/`
2. 把 `AGENTS.snippet.md` 的内容追加到当前会话环境的用户级指令文件。
3. 重开会话。

`AGENT_HOME` 默认对应 `%USERPROFILE%\.agents`，也可使用环境实际读取的目录。

## 卸载

```bash
bash install.sh --uninstall
```

若当初用了自定义指令文件：

```bash
bash install.sh --uninstall --instructions-file /path/to/AGENTS.md
```

或手动删除 `<AGENT_HOME>/skills/single-conversation-subagent/`，并从用户级指令文件删除带 `single-conversation-subagent` 标记的整段内容。

## 行为边界

- 简单问答、单步查询、纯解释性对话也必须先四选一，不得自动跳过；选快速处理或只调查后，不强行起协调层，避免为小问题增加两层 Agent 开销；一旦选择标准执行/长任务，立即按两级流程执行。
- 工作区自己的指令文件加载在用户级规则之后，可以覆盖或收窄这条默认规则。
- 执行子 Agent 的名称统一用 `<任务>子 Agent`，不要固定使用模型名或其他固定称呼。
- 同一项目仍使用桥接模式时先明确冲突处理与文件名；未经用户明确授权，不改默认 `<docs-root>/agent/AGENT_PROJECT.md` / `AGENT_COORDINATION.md`，也不手工修改桥接管理的受管文件。
- 原生模式没有桥接式父子目录独立核验；需要严格进程级审计时，继续使用三层桥接模式。
- skill 和协议不包含密钥、Cookie 或项目私有数据；不要把实际项目的 `<docs-root>/agent/AGENT_PROJECT.md` / `AGENT_COORDINATION.md` 打进分发包。
