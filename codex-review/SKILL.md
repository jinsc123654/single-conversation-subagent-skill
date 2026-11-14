---
name: codex-review
description: 让 Codex 以只读方式独立审查已完成的任务、未提交改动、指定修订或 commit，输出逐项缺陷判定与最终 ACCEPT/REJECT；审查模型固定为 GPT-6.1 Sol（`gpt-6.1-sol`）。用户说“让 Codex 审查/复核/看看”“Codex review”“独立审查这个改动/任务”“提交前 review”“验收前找第三方看一遍”时使用；也用于审查基线一致时的追问、补查与配额恢复续跑。
---

# Codex 独立审查

把「Codex 做审查、主对话只做范围与证据、结论以 Codex 为准」固化成流程。适用于任意项目的实现后独立复核与提交前检查；在 esp32_epd 里对应 WP 的实现后复核与 Rn 独立审查。

## 角色边界

- **审查者是 Codex**。主对话负责：确定范围、准备证据/哈希、启动 Codex、校验它是否真的给出结论、转述并落档。
- 主对话不得替 Codex 下结论，不得把实现方自测（smoke/宿主回归）直接写成 Codex 的 PASS。
- 审查期间不改源码、不构建/烧录、不访问串口/板卡；只做只读检查（读文件、哈希、diff、grep），运行类回归由外部先跑好，缺的标 NOT_RUN。
- 审查者只做审查，不再启动新的 Codex 会话；启动、校验与落档由编排者（主对话）完成。
- 本流程不使用子 Agent 代审（既有约定：审查必须由 Codex 独立完成）；Codex 也不得再委派。
- **审查模型固定为 GPT-6.1 Sol**（`gpt-6.1-sol`，2026-10-25 用户指定）。每次显式传 `-m gpt-6.1-sol`，不要依赖 `~/.codex/config.toml` 的本机默认模型（可能被改成别的，如 `gpt-6-astra`）；只有用户明确指名换模型时才换，并在报告里写明。

## 1. 确定审查对象

| 用户说法 | 对象 | 取范围 |
|---|---|---|
| 审查当前改动 | 未提交工作树 | `git status --short`、`git diff --stat`、`git diff --cached --stat`、`git ls-files --others --exclude-standard` |
| 审查某个修订（如 WP-xx/Rn） | 某个工作包/修订 | 读项目协作记忆与证据目录（esp32_epd：`docs/agent/AGENT_PROJECT.md`、`AGENT_COORDINATION.md` 对应节、上一轮审查报告、`docs/agent/evidence/WP-xx/`；其他项目按其文档约定） |
| 审查 commit | 单个提交 | `git show --stat <sha>`；必要时 `codex exec review -m gpt-6.1-sol --commit <sha>` |
| 审查相对分支 | 分支差异 | `git diff <base>...HEAD --stat`；必要时 `codex exec review -m gpt-6.1-sol --base <base>` |

先确认范围里混入了哪些**无关改动**（仓库常叠加多个工作包的未提交改动），在 brief 里明确列出“只看这些文件/接口”，否则 Codex 会把别的改动算进来。

正式审查前记录源码哈希：`sha256sum <files>`。上一轮证据里通常有 `source-sha256.txt`，用它证明审查的是当前版本。

### 基线冻结

brief 里的哈希写好后、发送前再核对一次。若与 brief 不一致，说明工作树在准备期间被其他会话改过——不要发，重新取基线或等改动落定。Codex 审查期间也可能继续改（看 mtime/哈希）；跑完再核对一次，不一致就把结论标为 **STALE**，不能当作当前版本的验收结论。

- 审查对象要固定身份：工作树记 HEAD 与范围内 staged/unstaged/未跟踪状态；commit 记完整 OID 并写明是整提交还是相对哪个父提交（合并提交必须写明比较父）；分支差异记解析后的 base、head、merge-base OID 与比较方式（如三点）。分支名只作说明，不作身份。
- 前后哈希一致只证明两个检查点一致。审查期间观测到写入时（mtime/哈希变化、其他会话操作），即使结束时恢复原样，也标 **STALE**——报告可能混用了中间版本，重审必须新开会话。

## 2. 准备有界 brief

用 `references/brief-template.md` 生成 prompt；核心是：

- 审查者身份与只读边界（不写、不构建、不烧录、不联网、不委派）。
- 对象与背景：上一轮报告路径、已知缺陷编号、修复声称。
- **只读清单**：精确到文件路径；明确“不要读整棵依赖树/NimBLE 源码”，控制配额。
- 逐项核查清单：每条缺陷对应的状态机/锁/回调路径。
- 验证方式：只做只读检查（读文件、哈希、diff、grep）；运行类回归由外部先跑好，结果作为待核对材料贴进 brief；Codex 不自行构建/测试/联网，未提供的运行证据标 NOT_RUN。
- 输出格式：逐项 `[P?] 标题 — FIXED/PARTIAL/NOT FIXED — file:line`；新问题 P0–P3；最后一行 `结论: ACCEPT` 或 `结论: REJECT`。

把实现方已跑过的测试结果作为**待核对的材料**给它，不要写成“已通过”。

## 3. 调用 Codex（只读）

Codex 不在 PATH，二进制：`/usr/lib/chatgpt/resources/codex`（ChatGPT 桌面版内置，2026-10-24 实测 0.163.0-alpha.10.2）。

- `-C` 必须是 Codex 信任的目录（通常为已有 git 仓库）；非 git/未信任目录会报 "Not inside a trusted directory"，改从信任的仓库目录启动（brief 里用绝对路径），或加 `--skip-git-repo-check`；不要修改 Codex 的信任配置。

```bash
cd <repo> && cat <<'PROMPT' | /usr/lib/chatgpt/resources/codex exec \
  -m gpt-6.1-sol -s read-only -C <repo> -c model_reasoning_effort="high" \
  -o /tmp/codex-review-<topic>.md - 2>&1
<brief>
PROMPT
```

- `-m gpt-6.1-sol` 是本技能固定的审查模型（GPT-6.1 Sol）；不传就吃 `~/.codex/config.toml` 的本机默认，可能不是 Sol。
- `model_reasoning_effort` 默认 `high`；该模型支持 low/medium/high/xhigh/max/ultra，配额紧时降到 `medium`，需要更深推理时升到 `xhigh`，并在报告里记下实际档位。
- prompt 走 stdin（`-`）；`-o` 把 Codex 的最终消息单独落盘，便于提取结论。
- 只读沙箱：仓库不可写，gcc/ASan 常因临时文件被拒；宿主回归先在外部跑好，把输出贴进 brief。
- 预计超过 5 分钟用后台 Bash（输出重定向到 `/tmp/...log`），轮询；不要在前台一次等 10 分钟。
- 不让 Codex 读无关大目录；它读得越多，越容易撞配额。
- 启动头部会打印 `session id: ...` 与 `model: ...`：把 session id 记进报告头，供同一基线的追问续跑；先核对 `model: gpt-6.1-sol` 与实际推理档位，不符就停下重跑；若 `-o` 文件未生成，从 stdout 尾部提取最终消息。
- 需要快速看 diff 时可用 `codex exec review -m gpt-6.1-sol --uncommitted` / `--commit` / `--base`；正式修订审查用自定义 brief，因为内置 review 不保证逐项格式。

### 会话复用：基线一致才续跑，否则新开

判定依据是**审查基线**，不是“同一个工程”，也不是单独一份源码哈希。基线 = 项目绝对路径 + WP/Rn（或用户给的修订标识）+ 审查对象（工作树 HEAD / commit OID / 分支三点比较）+ 只读清单与包含排除规则（`source-sha256.txt`）+ 验收标准。只有基线完全一致才允许续跑：

- **基线一致**：追问、补查、换角度调查、要求补证据时，一律 `codex exec resume <session-id> "<增量问题>"`，不新开会话。prompt 只写增量内容（新增证据的路径/哈希/来源），不要重贴整份 brief；`-o` 换新文件名，别覆盖上一轮报告。
- **基线变化**：必须新开会话，brief 里引用上一轮报告路径与缺陷编号。旧会话记的是旧文件内容，续用会拿旧印象判新代码，把已修项误判成 NOT FIXED。新增文件被 glob/自动发现机制加载、依赖或配置变化、换了审查范围或验收标准，都算基线变化。
- **审查期间有写入**：即使结束时哈希恢复原样，本次结论也标 **STALE**——报告可能混用了中间版本，不能当作该基线的验收结论；重审要新开会话。
- **例外**：确认发生过上下文压缩、剩余窗口不足以完成下一步补查，或用户要求盲审（换一个没看过这版代码的审查者）时新开，并在报告里注明原因。窗口与终态上下文以当次实际值判断，不要把某个模型的数字写成通用阈值。
- 续跑也显式传 `-m gpt-6.1-sol` 与 `-c model_reasoning_effort="high"`（`codex exec resume` 支持这两项；没有 `-s`/`-C`，沙箱与工作目录沿用原会话）。启动头核验实际 `model:` 与推理档位，不符就停。
- 报告头记 `session id` 与是否续跑；正式审查按 id 精确 resume，禁止用 `--last` 挑会话。

## 4. 验收 Codex 的输出

- 读最终消息（`-o` 文件）与原始日志；确认最终消息里有明确的 `结论: ACCEPT` 或 `结论: REJECT`（可被 Markdown 加粗包裹）。
- 没有最终结论（中断、配额、只留部分分析）→ 标记 **INCOMPLETE**，不得写成 ACCEPT/REJECT。
- 如果输出含 `You've hit your usage limit`：本次调用标 **INCOMPLETE**，记录提示的重置时间、session id、原始日志与已完成内容；重置前不重试，不自动换模型/供应商。恢复后先按 §3「会话复用」核对基线：基线一致才续跑，基线变化按新修订新开，原会话不可恢复则新开并在报告里记失败原因。
  ```bash
  /usr/lib/chatgpt/resources/codex exec resume <session-id> \
    -m gpt-6.1-sol -c model_reasoning_effort="high" \
    "继续完成 <topic> 审查并给出最终结论"
  ```
  续跑用新的 `-o` 文件名；只缩小读取清单，不能缩小原验收范围，缺口仍在就保持 INCOMPLETE。
- PARTIAL/NOT FIXED 必须能从 file:line 复述出反例路径；无法复述的，要求 Codex 补证据或降级为 INCOMPLETE。

## 5. 落档（按用户要求）

- 对话里先给：结论、逐项判定、新问题、NOT_RUN、原始日志路径。
- 正式审查默认写进**项目自己的报告/证据目录**；esp32_epd 写：
  - `04_test/report/YYYY-MM-DD_<topic>_review.md`：结论 + 逐项 + 新问题 + 验证限制 + 证据。
  - `docs/agent/evidence/WP-xx/Rn/codex-review/`：原始日志、最终消息、源码哈希。
  - 其他项目按其文档约定；没有约定时放 `docs/reviews/`。
- 若审查期间基线变化（STALE），不要写成该修订的正式报告；保留原始日志与最终消息，在对话里标 STALE/INCOMPLETE，等版本冻结后重跑。
- 用 Write/Edit 落文件，不用 Bash 重定向写源码/配置（本机 Mimosa 钩子会拦）。
- 报告注明审查者=Codex（模型 `gpt-6.1-sol`、实际推理档位）、会话 id 与是否续跑、基线标识（WP/Rn 或对象 OID）、审查时源码哈希；不要把主对话的分析混进 Codex 的结论。

## 常见坑

- **配额**：与 ChatGPT 计划共享；一次深度审查可能 5–10 万 token。先缩范围，再跑。
- **read-only 沙箱**：不要让 Codex 编译/写临时文件；运行类验证由外部完成，沙箱拒绝写盘是预期限制。
- **锁被 stub**：如果回归脚本把锁替换成空实现，并发路径不会被覆盖；在 brief 里点名要求审查真实锁序。
- **无关改动**：仓库常叠加多个工作包/主题的未提交改动，必须显式划界。
- **结论伪造**：只认 Codex 最终消息里的结论行；主对话自己的代码核对只能作为“复核意见”，不能替代审查结论。
- **模型漂移**：漏传 `-m` 或被本机配置覆盖时会退回默认模型（本机曾见 `gpt-6-astra`）；以日志头 `model:` 行为准，不是 `gpt-6.1-sol` 就重跑，不落正式报告。
- **会话混用**：同一工程 ≠ 同一会话。跨修订复用旧会话，会拿旧文件印象判新代码；判定看项目 + WP/Rn + 审查对象 + 只读清单 + 验收标准是否完全一致（见 §3「会话复用」）。
