# 审查 brief 模板

把 `<...>` 替换为实际内容；保持只读清单尽量短。

```text
你是独立审查者。只读审查：不修改任何文件，不构建/烧录固件，不访问串口或板卡，不调用子 Agent/DeepSeek/MCP 委派，不联网。

## 对象
- 仓库：<repo 绝对路径>
- 审查对象：<WP-xx/Rn / uncommitted / commit OID / base...head>
- 基线标识：<与报告头、session id 一起登记，用于判断能否续跑>
- 上一轮报告：<path>（结论：<...>）
- 当前源码哈希：<source-sha256.txt 路径 + 哈希>
- 只读清单与包含/排除规则：<file list + 自动发现规则>
- 验收标准：<...>

## 范围（只读这些）
- <file:line / file>
- ...
明确：不要读整棵 <dependency> 源码；不要把以下无关改动算进来：<list>

## 已知缺陷（逐项核查）
1. [P?] <标题>：<上一轮原文摘要 + 期望修复行为>
...

## 验证
- 只读检查：<哈希/diff/grep 命令>
- 运行类回归（外部已跑）：<命令与输出摘要>；Codex 不自行构建/测试/联网。
- 未提供的运行证据标 NOT_RUN，并说明原因。
- 实现方声称：<测试结果>；这是待核对材料，不是结论。

## 输出
- 逐项：[P?] 标题 — FIXED / PARTIAL / NOT FIXED — file:line 证据与推理；PARTIAL/NOT FIXED 给反例或复现路径。
- 新问题：P0–P3 + file:line + 建议。
- 最后一行：结论: ACCEPT 或 结论: REJECT（一句话理由）。
- 附：实际执行的命令与结果（或 NOT_RUN 原因）。
```
