---
name: single-conversation-subagent
description: 两级协作（单对话 subagent/主对话协调/执行子 Agent/验证范围）：新任务必问四选一，仅明确“不用问，直接做”可跳过；记忆在 docs-root/agent，标准/长任务再问验证范围与 Codex 收尾审查。
---

# 两级协作

仅主对话协调、创建原生子 Agent 并独立验收；子 Agent 只分析/实现/测试，不再委派。

## 提问

顺序：四选一→验证范围→Codex 联动；后两问仅标准/长任务适用。

- 每个新任务先四选一，直接命令也问，不按大小/类型跳过；同任务不重问。只标一个推荐并默认勾选，拿不准推荐标准执行；推荐不免问。
- 快速处理：主对话直接做，无子 Agent。标准执行：问完再计划、执行。长任务：分阶段并用执行子 Agent。只调查：不改文件，范围大可派只读分析子 Agent。
- 唯一例外：明确“不用问，直接做”→快速处理；实际需标准/长任务仍完成适用后两问。
- 验证唯一推荐“快速冒烟（本轮）+ 定向回归（收尾）”；冒烟只读；全量/稳定性也收尾，不每轮重复；未选操作不执行，新增走新修订。“不做测试”/“只记录方法”互斥且均排除测试，标 NOT_RUN；后者只记命令/环境/预期结果，不标 PASS。破坏性操作单独确认。
- 确认本地已注册 codex-review 才追加“收尾是否用 Codex 独立审查？”；未知可查本地安装辅助确认，仍未知/未装跳过。仅“不使用”（唯一推荐/默认）和“使用”。明示要求则直接执行、不再问，默认不覆盖。
- 三问仅在用户明确不选或客户端报告15秒倒计时结束后按各问推荐/默认继续；等待不落默认值、不重复追问，倒计时由客户端实现。

Codex 是外部只读审查者，由主对话按 codex-review 启动，非子 Agent；子 Agent 不得启动，审查者不再启动新会话；逐项判定并落档 ACCEPT/REJECT。同工作包/修订只审一次。

## 执行与记录

计划/派发/迟到回复/异常前，按需读 [完整协议](references/protocol.md) 对应节并遵守其验证选项、预算/资源、字段、模板、边界、去重、handoff、恢复及终态规则；路径以本文为准。

核对证据/产物，无证据不标 PASS；区分 PASS/FAIL/BLOCKED/NOT_RUN/SUBSTITUTED。需子 Agent 却无法创建→BLOCKED；仅用户授权可回退。委派不扩权；调查只读，测试不改源码、不重建/换镜像，实现只改允许文件；同文件/设备串行。子 Agent 默认继承 provider/model；固定模型定义先改继承或按用户指定覆盖。用户明确指令优先。

## 路径

记忆根 = docs-root/agent：docs-root 取首个存在的 00_doc/00_docs/docs/doc；均无则新建 docs。AGENT_PROJECT.md、AGENT_COORDINATION.md、evidence/ 都在 docs-root/agent/。

WP 审查报告固定 04_test/report/。其余正式文档落 docs-root（不是 agent/）；不因目录规则自动迁移既有文件。Obsidian vault 根=docs-root；只读视图在 docs-root/agent/acceptance/。

标准/长任务加载正文；快速/只调查只用 AGENTS，不加载正文/协议；规则变更新会话生效。
