当前 2.6.0 能支持主对话手工安排独立只读并行，但还缺少支撑多执行子 Agent 并发的执行实例、资源占用和结果归属模型。**推荐同时支持包内、跨包并发，以“子任务的一次执行实例”为调度单元，主对话统一编排并串行登记状态。**

本次仅检查指定文件及基线：HEAD 符合，工作树 clean，哈希清单中九个文件全部匹配。未修改文件、构建、联网、调用外部服务或委派。以下结论区分现有实现与推荐设计。

## 1. 当前协议的阻塞点与缺口

P1 表示并发模型实施前必须补齐；P2 表示影响使用或可观测性。

1. **[P1] 子任务缺少依赖和执行输入身份 — [work.py:51](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:51) — 无法形成可校验的 DAG。**  
   子任务只接受 `id/kind/allowed_files/acceptance`，没有依赖、输入基线、资源需求或输出契约。协议要求声明基线，但 MCP 没有对应结构，无法检查某次测试消费的是哪批实现结果。

2. **[P1] 没有执行实例，运行过程与最终结果混用 — [work.py:65](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:65)、[protocol.md:139](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/single-conversation-subagent/references/protocol.md:139) — 无法区分尚未启动、正在执行、执行失联和重试。**  
   子任务初始化为 `NOT_RUN`；回传示例中的 `id` 可以是 Agent ID，也可以是 T1。缺少明确的 `subtask_id → attempt_id → host_agent_id` 关系。

3. **[P1] 证据隔离到子任务，没有隔离到每次执行 — [protocol.md:118](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/single-conversation-subagent/references/protocol.md:118)、[work.py:130](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:130) — 同修订重试可能覆盖原证据。**  
   当前哈希检查能发现已登记证据变化，但不能识别“第二次执行覆盖第一次输出”或证明证据对应哪个输入快照。

4. **[P1] 租约是精确键互斥，缺少层级范围与执行归属 — [locks.py:18](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/locks.py:18)、[locks.py:36](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/locks.py:36) — 目录与子文件、不同硬件别名可能同时获得租约。**  
   `file:src` 与 `file:src/a.py` 不相互排斥；owner 是会话，不是执行实例。同会话不同子 Agent 使用同一 token 时，服务也无法区分它们。

5. **[P1] 缺少资源集合原子占用和过期后的运行确认 — [locks.py:28](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/locks.py:28) — 可能死锁或出现新旧执行者同时写。**  
   当前一次获取一个资源，容易发生 A 持设备等串口、B 持串口等设备。TTL 到期允许换 token，但旧进程可能仍在运行；token 校验只防止旧持有人释放新租约。

6. **[P1] 切修订和关闭没有执行静止屏障 — [work.py:179](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:179)、[work.py:209](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:209) — 仍有后台写入时可能重置结果或关闭工作包。**  
   `new_revision` 直接清空结果；收尾检查只排除 `FAIL/BLOCKED`，无法排除运行中的任务，也未要求逐项解释 `NOT_RUN/SUBSTITUTED`。

7. **[P1] 修订接口不能表达重新规划 — [work.py:146](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:146) — 无法结构化调整拆分、依赖、范围和验收。**  
   当前新修订保留原定义，只重置状态和证据。并发调度中发现共享文件或新增集成任务时，缺少受控修改定义的入口。

8. **[P1] CAS 已有，但执行回报和更新重试没有完整身份约束 — [work.py:158](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:158)、[work.py:217](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:217) — 机械刷新版本重试可能误用旧结果。**  
   全包 `expected_version` 能阻止覆盖，值得保留。缺口是更新没有 `attempt_id/expected_revision/operation_id`。R1 证据路径会被现有检查拒绝，但仅刷新 CAS、复用同名 T1，并不能证明回报属于当前执行。

9. **[P1] handoff 增加访问资格，没有转移唯一协调权 — [work.py:32](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:32)、[work.py:255](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/mcp_server/src/scs_mcp/work.py:255) — 新旧主对话可能同时继续派发。**  
   CAS 只能串行化数据库更新，不能阻止两个主对话分别启动真实 Agent。需要控制权代次和交接屏障。

10. **[P2] 缺少明确的并发编排规则 — [protocol.md:89](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/single-conversation-subagent/references/protocol.md:89)、[protocol.md:181](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/single-conversation-subagent/references/protocol.md:181) — 容易退化为逐个派发、逐个等待。**  
    现有示例不是禁止并发，但没有并发写入条件、全局并发预算、按完成顺序收割结果或失败隔离流程。`每次只用一路提问`约束的是问答通道，**不应解释成只能运行一个 Agent**。

11. **[P2] UI 无法展示实际并发和资源等待 — [backend.py:130](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/desktop_tool/src/scs_manager/backend.py:130)、[window.py:224](/home/jinsc/SDK/agent/repos/single-conversation-subagent-skill/desktop_tool/src/scs_manager/window.py:224) — 用户只能看到包状态和子任务数量。**  
    当前快照不返回执行实例、租约或等待关系。管理器还只接受 schema 1/2，数据库升级必须同步兼容。

## 2. 推荐设计与备选方案

### 2.1 并发单元与数据模型

首选支持：

- 同一个 WP 内，多个就绪子任务并发；
- 同一主对话、同一 canonical 项目内，多个 WP 的子任务并发；
- 两者共用一个并发预算和资源冲突域；
- 首版不支持跨项目联合调度，也不允许多个主对话共同协调同一个 WP。

工作包是目标与验收边界；修订是冻结的执行定义；子任务是 DAG 节点；**attempt 是一次真实执行**。

| 对象 | 必须字段与约束 |
|---|---|
| WP | `package_id`、当前修订、`version`、`controller_session`、`controller_epoch` |
| 修订定义 | 目标、验收、基线、范围、子任务 DAG、`definition_hash`；执行后不可原地修改 |
| 子任务 | `subtask_id`、kind、依赖、读写范围、资源需求、输出契约、是否必须完成 |
| attempt | `attempt_id`、所属 WP/修订/子任务、序号、控制权代次、输入快照、生命周期、结果 |
| 宿主执行 | `host_agent_id`、调用关联 ID、后台 job/process handle、最后观测时间 |
| 结果 | `result_id`、attempt、证据清单、输出哈希、主对话核验结论 |
| 资源占用 | canonical 资源、读写模式、attempt owner、token、到期时间、隔离状态 |
| 授权引用 | 用户授权来源、允许操作、验证范围、授权代次和撤回记录 |

关键不变量：

1. 一个 attempt 只属于一个 `(WP, revision, subtask)`，不得跨节点复用。
2. 同一节点最多存在一个未确认结束的 attempt。
3. 本地 `attempt_id` 必须有；拿不到真实 Agent ID 时单独记“不可得”，不得伪造。
4. 宿主会话即使被复用，新派发也必须建立新 attempt；首版可直接要求每次执行使用新子 Agent。
5. `version` 是状态写入的 CAS 计数；`revision` 是定义代次。普通状态变化不使其他在途结果失效。
6. 子 Agent 不直接更新 WP、顶层记忆或验收；主对话串行登记回报。

证据路径扩展为：

```text
<docs-root>/agent/evidence/WP-<id>/R<rev>/<subtask-id>/<attempt-id>/
```

每个目录有固定 manifest，记录定义哈希、实际输入快照、依赖结果 ID、执行者、命令/环境、输出及证据哈希。旧 attempt 目录不覆盖。

### 2.2 DAG、就绪队列与并发预算

依赖使用完整节点引用：

```text
(package_id, revision, subtask_id)
```

实际启动时，再固定所消费的 `result_id + output_digest`。禁止依赖含糊的“最新结果”。

主对话提出 DAG；MCP 负责确定性校验：

- 节点存在、无环、无自依赖；
- 跨包依赖位于同一项目和受控工作集合；
- 子任务范围不超父范围；
- 依赖修订和结果未被取代；
- 输入、资源和授权声明完整。

节点进入就绪队列须同时满足：

```text
依赖已由主对话核验
AND 授权有效
AND 输入快照有效
AND 没有未结束的旧 attempt
AND 当前修订允许继续执行
```

资源未获得时进入资源等待，不启动 Agent。

建议初始并发上限为 4，并受宿主已知容量、用户预算、CPU/内存、设备容量和主对话可处理结果预算共同限制。没有真实并发能力证据时取 1；本题给定的 ZCode 能力允许启用多路派发。

优先级由主对话决定，默认顺序：

1. 用户明确优先事项；
2. 解锁关键路径的任务；
3. 解除当前阻塞的短任务；
4. 其余任务，按等待时间逐步提高优先级。

资源冲突时换选其他就绪任务，避免队首阻塞。主对话可以收紧并发、调整独立任务顺序、串行化共享资源任务，无需重新询问用户。

必须回用户的事项包括：扩大目标或写范围、增加未授权测试或危险操作、明显突破预算、降低验收要求、选择互斥产品方案，以及无法继续真实子 Agent 执行而请求单级回退。

### 2.3 冲突控制

现有 leases 可以保留为兼容接口，但不足以承担新版并发占用。

新版需要：

| 项目 | 规则 |
|---|---|
| 文件/目录 | 规范化路径，目录覆盖后代；边界按路径段判断 |
| 读写 | 同资源 R/R 可并行，R/W、W/W 冲突 |
| 构建 | 独立构建目录可并发；共享构建目录、生成文件和全局缓存须声明冲突 |
| 设备 | 板卡、串口、烧录器通过别名表归并到物理资源及其关联资源 |
| 获取 | 一个 attempt 所需资源集合，在同一事务内全部获取或全部失败 |
| owner | 绑定 attempt 和控制权代次，不能只绑定 session |
| 等待 | 不持有部分资源等待剩余资源；队列由主对话维护 |
| 续租 | 在到期前完成；不支持可靠续租的宿主不得放任后台长执行 |
| 过期 | 不视为进程停止；存在未确认执行者时进入资源隔离 |
| 抢占 | 首版不抢占运行中的写任务；先停止、确认静止，再交接 |

还要检查路径别名、硬链接或其他可能映射同一对象的情况；无法可靠归一化时，拒绝并发写，或提升到共同祖先目录串行。

**租约仍是建议性协调机制。** token 能约束服务接受哪些状态操作，不能阻止旧进程继续写磁盘或操作设备。发现旧执行者状态未知时，不能仅凭 TTL 给新写者放行。

具体写入规则：

- 同一共享工作树、同一文件：一次只有一个写者，不采用“不同函数可以并行写”的例外。
- 同一 WP：可以并发修改互不重叠的文件，且声明隐含写入位置。
- 测试不能读取持续变化的输入；使用冻结产物，或持读占用等实现完成。
- Git index、checkout、合并、提交统一串行；默认由集成执行子 Agent 操作。
- worktree 可作为可选隔离方案；同一逻辑文件的多个改动最终仍须串行集成和验证。
- 设备跨项目使用必须进入同一权威资源域。首版未建立该域时，不支持跨项目并发设备操作；不能把项目 DB 租约说成全机互斥。

### 2.4 生命周期、结果与版本

生命周期和结果分开存储：

```text
PREPARED → LAUNCHING → RUNNING → DRAINING → ENDED
                  ↘ UNKNOWN
RUNNING → STOP_REQUESTED → DRAINING → ENDED
```

- `PREPARED`：已登记执行意图并占用资源，尚未调用宿主。
- `LAUNCHING`：已记录即将调用宿主；真实创建位于数据库事务之外。
- `RUNNING`：宿主执行身份已绑定。
- `DRAINING`：收到完成/停止消息，仍在确认后台进程、输出和资源静止。
- `UNKNOWN`：无法确认是否创建成功、是否仍运行或是否已经停止。
- `ENDED`：确认没有该 attempt 的继续写入活动。

明确未启动的 `PREPARED` 可以直接结束。`UNKNOWN` 不能自动释放执行槽和冲突资源，也不能自动重派。

结果继续兼容现有枚举：

| 情况 | 记录方式 |
|---|---|
| 执行中 | `result=null`，用生命周期表示进展 |
| 完成且证据满足契约 | 报告 `PASS`，另记主对话是否核验 |
| 明确执行失败 | `FAIL` |
| 外部条件阻塞、启动后取消或超时中断 | `BLOCKED`，附具体原因和已发生动作 |
| 确认从未执行，或用户明确不测 | `NOT_RUN`，附原因 |
| 被其他执行或方案取代 | `SUBSTITUTED`，必须引用替代项 |
| 部分完成 | 保存进度、产物和剩余项；不能把整个任务标 PASS |

重试创建新 attempt，保留旧结果。可确认没有副作用的瞬时失败，可在预定预算内重试一次；写入、设备操作或启动结果不明时，先核对现场。替代结果和 `NOT_RUN` 不自动满足下游依赖。

WP 保留现有状态机即可：

- 有运行、等待或部分成果：`IN_PROGRESS`，附运行计数和 `partial` 摘要；
- 没有可推进任务：`BLOCKED`，附原因；
- 全部必须项满足、例外明确处理、无在途执行：`REVIEW_READY`；
- 主对话最终验收后：`COMPLETED`；
- 放弃时先停止相关执行并确认静止，再 `ABANDONED`。

修订与更新规则：

1. 新修订可提交完整新定义；重新验证 DAG、范围和授权。
2. 首版要求同 WP 的旧执行全部静止后才能切修订；其他无冲突 WP 可继续运行。
3. 已有通过结果不自动跨修订继承；复用须显式记录来源及适用性核验。
4. 运行状态更新仍使用全包 `expected_version`，由主对话串行提交。
5. 新增 `expected_revision/controller_epoch/operation_id`。
6. 同一 `operation_id`、同一载荷返回原回执；同键不同载荷拒绝。
7. `VERSION_CONFLICT` 后重新检查控制权、修订和 attempt，不能只替换版本号重发。

handoff 必须转移唯一控制权。首版采用静止交接：相关执行全部结束后，原子切换 `controller_session` 并递增 `controller_epoch`，旧协调者随即失去写入和新派发资格。活动作业转移属于后续扩展。

### 2.5 主对话的可执行调度流程

```text
读取目标、授权、当前控制权和工作包摘要
由执行子 Agent 完成任务性调查
主对话形成 DAG、资源声明、验收和并发预算
MCP 校验并保存冻结定义

while 尚有未处理任务:
    收割宿主已经返回的消息
    对每条消息:
        核对 WP / revision / subtask / attempt / controller_epoch
        迟到或不匹配的消息进入隔离记录，不推进当前节点
        记录结果；确认后台活动是否静止
        静止后核验证据与输入/输出身份
        串行登记结果并释放对应资源

    处理授权撤回、超时、失联、资源隔离
    停止失败节点的后继；独立分支可继续

    if 接近上下文预算、进入审查或需要用户决定:
        停止受影响的新派发
        处理/停止相关在途执行
        写摘要和证据索引
        按条件等待、交接或请求用户决定
        continue

    从全局就绪队列选择本批任务
    对每个候选:
        原子登记 attempt 并占用完整资源集合
        冲突则记录等待原因，换选其他候选

    对本批已准备任务:
        先持久化 LAUNCHING
        在同一批中并行发起原生 Agent 调用
        绑定真实宿主身份
        按完成事件收割，不等待全批结束才推进后继

    没有可启动任务时:
        使用宿主支持的等待/检查点
        没有运行者且没有就绪节点，则记录 BLOCKED

全部满足后:
    执行集成与所选验证
    冻结相关输入，完成已授权的收尾审查
    主对话核验关键证据并关闭 WP
```

MCP 仅做校验、原子占用、状态记录和查询。候选选择、Agent 创建、等待、重试和停止均由主对话通过宿主工具执行。

上下文预算沿用现有 1–2 KB 回报上限，但要增加整批预算。主对话只保留 DAG 摘要、在途实例、异常、决定和证据路径。接近现有 60%–70% 阈值时停止新派发；不把所有子 Agent 历史重新读入。

长测试即使子 Agent 已返回，后台 job 仍算活动执行。宿主没有可靠唤回或续租能力时，应采用有界等待或可检查点任务；不能依赖 MCP 自动唤醒对话。

“禁止重复提交”分两层：

- MCP 写入通过 `operation_id` 去重；
- Git 提交、推送或其他外部副作用先登记操作意图，单一执行者完成并核对结果。调用结果未知时先查实际状态，不能承诺外部动作天然恰好执行一次。

### 2.6 安全和授权

执行子 Agent 的权限取交集：

```text
用户实际授权
∩ 当前有效授权代次
∩ WP 范围
∩ 子任务范围
∩ 宿主实际权限
```

租约、工作包存在、验证选项和并发度设置都不产生新增授权。

- 调查只读；测试沿用“不改源码、不重建、不换镜像”；需要构建时建立具有明确输出目录的独立实现/构建任务。
- 危险操作引用用户对具体操作、目标和范围的授权，不能从“全量验证”推导烧录权限。
- 子 Agent 不提问、不验收、不启动审查、不改顶层规划，不继续委派。
- 用户撤回授权时，主对话立即停止相关新派发，登记撤回并停止受影响执行；已发生动作如实保留。
- 越权写入或不明设备动作触发相关资源隔离和重新验收；不自动覆盖、回滚其他并发任务的改动。
- 首版保留本地单用户、合作参与者边界。MCP 状态校验不应描述成宿主沙箱或恶意 Agent 隔离。

### 2.7 备选方案

| 方案 | 优点 | 限制 |
|---|---|---|
| **首选：全局队列，包内与跨包并发** | 直接满足长任务编排目标；所有冲突统一检查 | 必须实现 attempt、资源集合和静止屏障 |
| 首版只允许一个活动 WP，包内并发 | 实施面较小，仍可真正多子 Agent 并发 | 暂不支持跨包流水线 |
| 独立 worktree 并发，串行集成 | 适合复杂代码改动和不同方案探索 | 增加集成成本；设备与共享缓存仍需协调 |
| 保持现有 schema，纯主对话手工台账 | 可用于受控只读并行 | 不宜作为持久化并发模型交付 |

## 3. 最小实施清单

| 分组 | 必须 | 可选 |
|---|---|---|
| **Skill** | 明确长任务默认主动识别并发机会；全局并发预算；批量发起、按完成收割；子 Agent 只回报；上下文与失败隔离规则 | 按资源、耗时自动建议并发度 |
| **协议** | 定义 WP/修订/子任务/attempt 身份；DAG；读写资源；结果与生命周期分离；迟到结果、取消、超时、重试规则；切修订/关闭/审查/交接屏障 | 活动作业跨对话转移；复杂 worktree 流程 |
| **MCP 定义** | 扩展子任务字段；保存基线和定义哈希；支持提交新修订定义；兼容旧枚举 | 更丰富的成本估计 |
| **MCP 运行记录** | attempt 创建与转换、真实宿主关联、后台 job、结果核验；`operation_id`；修订和控制权代次检查；运行摘要查询 | 增量订阅 |
| **MCP 资源** | 层级范围、R/W、完整资源集合原子获取、attempt owner、续租、未知持有者隔离、硬件别名归并 | 持久化公平队列；跨项目共享设备注册域 |
| **MCP 收尾** | 未静止不得切修订/关闭/交接；收尾完整性检查；交接撤销旧协调权 | 更细的任务级 CAS |
| **桌面 backend** | 兼容新 schema；返回节点/attempt/等待/租约；分页查询和快照时间 | 历史统计 |
| **桌面 UI** | 可展开 WP→任务→attempt；运行/等待/未知计数；资源等待对象；证据链接；运行中禁止直接关闭 | DAG 图、时间线 |
| **Obsidian** | 增加并发状态表、执行实例、资源等待与冲突页；显示生成时间和来源版本；保留批注保护 | Mermaid DAG、历史趋势 |

MCP 可以新增“执行准备”“执行状态登记”“运行摘要查询”接口；其中执行准备负责原子记录和资源占用，**不调用宿主 Agent**。生命周期变化至少记录执行、停止、静止确认、结果、租约冲突/过期、授权撤回和控制权转移事件，并带关联身份，避免记录提示词全文。

状态表建议包含：

```text
WP/R | 子任务 | attempt | 宿主 ID | 生命周期 | 报告结果
主对话核验 | 等待原因 | 阻塞者 | 资源 | 最后观测 | 证据
```

UI 显示“最后报告运行中”“租约已过期、执行未知”，不能推断宿主仍在线。现有 8 秒刷新可以保留；心跳不必触发全部 Markdown 重生成。

迁移按保守方式处理：

1. 新能力使用明确的并发模型版本，SQLite 升级至新 schema。
2. 旧 WP 保留原修订、结果和证据路径；不虚构历史 Agent 或 attempt。
3. 旧包默认使用 legacy 模式，并发度 1。若继续按旧接口执行，保留原行为。
4. 启用新并发模型时，先确认无活动执行，再创建新修订并补齐 DAG、基线和资源声明。
5. 旧证据可以引用，但必须注明 legacy 来源；不自动提升为新模型已核验结果。
6. 升级同步更新管理器版本识别；回滚恢复匹配的数据库备份。
7. 同步协议副本、README 和 MCP 参考。`mcp.md:16` 的“超时自动继续上次选项”与现有显式确认规则冲突，应统一为超时不产生新授权。

## 4. 反例、检测与恢复

| 具体场景 | 检测 | 恢复 |
|---|---|---|
| A 写 `src/`，B 写 `src/a.py` | 层级 W/W 冲突 | 一个等待；或重拆为确实不重叠范围 |
| 两个任务声明不同源码，却同时写 `build/generated.h` | 隐含输出声明、实际改动核对 | 隔离构建目录；受影响结果失效后重验 |
| 测试读取一半旧源码、一半新源码 | 输入 manifest 与实现输出不一致 | 冻结输入后重跑，原结果保留但不接受 |
| A1 超时，A2 启动；A1 随后覆盖 `result.json` | attempt 独立目录、活动实例唯一约束 | 确认 A1 停止后才能 A2；隔离迟到回报 |
| R1 的 T1 迟到，当前已是 R2 的 T1 | 完整归属与定义哈希校验 | 拒绝推进 R2，登记迟到结果 |
| 两条回报都基于 version 7 | CAS 返回 `VERSION_CONFLICT` | 主对话串行核对并应用尚未登记的操作 |
| MCP 更新成功但回执丢失 | `operation_id` 查询/重放 | 返回原回执，不重复变更 |
| Agent 已创建，但宿主调用回执丢失 | `LAUNCHING` 无法绑定真实结果 | 标 `UNKNOWN`，查宿主调用记录；不盲目重派 |
| A 持板卡等串口，B 持串口等板卡 | 禁止部分占用等待 | 完整资源集合原子获取；冲突则全部不占用 |
| 租约到期，但旧测试进程仍操作板卡 | 到期与静止状态分别记录 | 隔离资源，确认进程终态后重新获取 |
| 旧主对话与 handoff 后的新主同时派发 | 控制权代次不匹配 | 拒绝旧代次写入；核对已启动的旧执行 |
| 子 Agent 返回“完成”，后台测试仍在运行 | job handle 与进程终态核对 | 保持活动执行，不能关闭或释放设备 |
| 多 Agent 同时提交 Git | 仓库元数据占用、操作意图记录 | 单一集成执行者串行处理；结果未知先核对 HEAD |
| 多路日志和完整历史持续进入主对话 | 批量摘要大小与上下文阈值 | 停止新派发，只保留异常和证据索引，静止后交接 |
| 一个任务失败，其他任务继续消费其半成品 | 依赖只接受已核验结果 | 阻塞后继；独立分支继续，集成前重验 |
| 用户中途禁止设备测试 | 授权代次检查与撤回事件 | 停止相关执行，记录实际动作，静止后释放资源 |

## 5. 只读验证结果与实施验收边界

本次已核实：

- HEAD 为 `3646b25ad82c97ace0353c4477d1c3cce985efc9`，检查前后工作树 clean。
- 哈希清单覆盖的九个文件全部匹配。
- `_tasks` 当前拒绝依赖、资源和执行实例等新增字段。
- `work_package_update` 已有全包 CAS；新修订只重置原定义的结果。
- 租约按精确资源键查询，归属 session，过期后可更换 token。
- handoff 没有撤销原协调者资格。
- 桌面快照和表格没有执行实例及租约数据，管理器仅接受 schema 1/2。
- `docs` 协议副本存在恢复、审查注册及链接等差异，本报告按 `references/protocol.md` 判断。

实施后的必要验收用例应覆盖：真实并发执行、跨包资源冲突、目录与子文件互斥、原子资源集合、启动回执丢失、TTL 到期旧进程存活、取消后静止确认、迟到结果、CAS 冲突与幂等重放、控制权转移、旧包迁移。**本轮均未执行这些运行验证。**

受只读清单限制，本次未检查 storage、证据验证器和 Obsidian 生成器源码；有关这些组件的新增事务和投影要求属于实施规格。

推荐方案已明确身份、资源、状态、授权及恢复边界，可以进入实现。ACCEPT 针对该方案的可实施性；2.6.0 的并发能力仍须完成上述必须项并通过运行验收。

结论: ACCEPT