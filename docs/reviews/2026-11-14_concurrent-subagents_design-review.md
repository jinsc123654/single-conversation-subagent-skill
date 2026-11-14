# 长任务多子 Agent 并发设计审查（2026-11-14）

- 审查者：Codex（`gpt-6.1-sol`，reasoning `high`，read-only）
- 会话 id：`01a1daa3-2cae-76d0-96c6-c8fcdf1f829b`
- 基线：`main` @ `3646b25ad82c97ace0353c4477d1c3cce985efc9`（2.6.0），工作树 clean；9 个只读文件哈希核对一致
- 结论：**ACCEPT**（推荐设计可实施；2.6.0 现状仍不具备并发执行模型，需完成下列 P1 必须项）

## 核心建议

同时支持**包内并发**与**同一项目内跨包并发**，以“子任务的一次执行实例（attempt）”为调度单元；主对话统一编排并串行登记状态，MCP 只做校验、原子占用、状态记录和查询，不创建/调用 Agent。

数据模型需要把当前“子任务状态”拆成：

- 工作包：目标与验收边界，增加 `controller_session`、`controller_epoch`
- 修订：冻结的执行定义，含子任务 DAG、基线、范围、`definition_hash`
- 子任务：依赖、读写范围、资源需求、输出契约、是否必须完成
- attempt：所属 WP/修订/子任务、序号、控制权代次、输入快照、生命周期、结果
- 宿主执行：`host_agent_id`、调用关联 ID、后台 job/process handle、最后观测时间
- 结果与资源占用：证据清单、输出哈希、租约 owner=attempt、TTL、隔离状态
- 授权引用：用户授权来源、允许操作、验证范围、授权代次与撤回记录

关键不变量：一个 attempt 只属于一个 `(WP, revision, subtask)`；同一节点最多一个未确认结束的 attempt；证据目录按 attempt 隔离；子 Agent 不直接更新 WP、顶层记忆或验收；`version` 是 CAS 计数，`revision` 是定义代次，更新增加 `expected_revision/controller_epoch/operation_id`。

## 调度与冲突

- 就绪条件：依赖已核验、授权有效、输入快照有效、无未结束旧 attempt、当前修订允许继续。
- 资源未获得时进入等待，不启动 Agent；一个 attempt 的资源集合必须原子获取或全部失败，禁止持有部分资源等待。
- 默认并发上限建议 4，受宿主容量、用户预算、CPU/内存、设备容量和主对话结果预算限制；无真实并发能力证据时取 1。
- 优先级：用户明确优先事项 > 解锁关键路径 > 解除阻塞的短任务 > 其余按等待时间。
- 文件/目录：目录覆盖后代；R/R 可并行，R/W、W/W 冲突；共享构建目录、生成文件、全局缓存必须声明冲突；设备用别名表归并到物理资源。
- 租约仍是建议性协调：TTL 到期不等于进程停止；旧执行者状态未知时进入资源隔离，不能仅凭 TTL 放行新写者；首版不抢占运行中的写任务。
- 生命周期：`PREPARED → LAUNCHING → RUNNING → DRAINING → ENDED`，另有 `UNKNOWN`；`UNKNOWN` 不自动释放执行槽/资源，也不自动重派。结果枚举继续兼容 `PASS/FAIL/BLOCKED/NOT_RUN/SUBSTITUTED`，重试创建新 attempt。
- 切修订、关闭、交接前必须有执行静止屏障；首版要求同 WP 旧执行全部静止后才能切修订，其他无冲突 WP 可继续。handoff 首版采用静止交接，原子切换 `controller_session` 并递增 `controller_epoch`。

## 主对话编排流程

主对话读取目标/授权/控制权 → 由子 Agent 完成调查 → 形成 DAG、资源声明、验收和并发预算 → MCP 校验并冻结定义 → 循环：收割宿主消息、核对 `WP/revision/subtask/attempt/controller_epoch`、隔离迟到结果、确认后台静止、核验证据、串行登记并释放资源；失败则停止其后继，独立分支继续；接近上下文预算/审查/用户决策时停止新派发并写摘要；从全局就绪队列批量准备并并行发起原生 Agent 调用；没有可启动任务时等待/检查点或记 `BLOCKED`。

## 当前 2.6.0 的 P1 缺口

1. 子任务没有依赖、输入基线、资源需求、输出契约，无法形成可校验 DAG。
2. 没有 attempt 概念，无法区分未启动、运行中、失联和重试。
3. 证据只隔离到子任务，同修订重试可能覆盖原证据。
4. 租约是精确键互斥，目录与子文件不互斥；owner 是 session，不是 attempt。
5. 缺少资源集合原子占用和过期后的运行确认。
6. 切修订、关闭没有执行静止屏障。
7. 修订接口不能表达重新规划（调整 DAG/依赖/范围/验收）。
8. 已有全包 CAS，但更新缺少 `attempt_id/expected_revision/operation_id` 身份约束。
9. handoff 只增加访问资格，没有转移唯一协调权。
10. 协议没有明确的并发预算、批量发起、按完成收割和失败隔离流程；`每次只用一路提问`只约束问答通道，不应解释成只能运行一个 Agent。
11. 桌面/Obsidian 无法展示 attempt、租约、等待和冲突。

## 迁移与验收

- 新能力使用明确的并发模型版本并升级 SQLite schema；旧工作包默认 legacy、并发度 1，不虚构历史 attempt。
- 启用新模型时先确认无活动执行，再创建新修订补齐 DAG、基线和资源声明；旧证据可引用但标注 legacy。
- 管理器需兼容新 schema 并展示 WP→子任务→attempt、运行/等待/未知计数、资源等待对象；运行中禁止直接关闭。
- 必测：真实并发执行、跨包资源冲突、目录/子文件互斥、原子资源集合、启动回执丢失、TTL 到期旧进程存活、取消后静止确认、迟到结果、CAS 冲突与幂等重放、控制权转移、旧包迁移。
- 另发现 `mcp.md:16` 的“15 秒超时自动继续上次选项”与现有显式确认规则冲突，应统一为超时不产生新授权。

## 证据

- 完整 Codex 报告：[codex-final.md](evidence/2026-11-14_concurrent-subagents/codex-final.md)
- 原始日志：[conversation.log](evidence/2026-11-14_concurrent-subagents/conversation.log)
- 源码哈希：[source-sha256.txt](evidence/2026-11-14_concurrent-subagents/source-sha256.txt)

本轮为只读设计审查，未修改文件、未构建、未运行并发验收。
