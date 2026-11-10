# 2.0.0 改造与验收记录

日期：2026-11-10（Asia/Shanghai）。代码基线：`a1ca6dd68547676387be30a656d0b4e3332e40a3`。开发分支：`feat/mcp-hybrid`。未提交、未推送、未覆盖用户配置。任务 A/B 的可运行实现及 C 的文档/安装工作已完成；桌面客户端真实 UI 验证仍为 NOT_TESTED，不能称为全部客户端验收通过。

## 功能与设计决定

Skill 负责两级协作策略；可选 MCP 使用官方 Python SDK 2.3.0、stdio、SQLite schema 1 管理交互及确定性状态/证据；宿主原生 Subagent 执行，主对话最终验收。服务共 14 个工具、2 类只读资源，不创建 Agent，不执行测试命令，不提供 HTTP 或云端服务。

交互顺序为宿主原生提问、声明支持的 MCP 表单、完整文本回退。文本回退必须结束当轮等待明确回复。推荐值仅为 UI 默认值，模型验证仍要求答案字段；空接受、取消、拒绝、错误和超时均不能确认推荐项。Resolver/Elicit 兼容新旧协议；问题 ID 唯一，回复幂等、替换后迟到隔离；事务 claim 防并发重复 UI 请求。

会话由宿主命名空间、真实对话 ID 与 canonical 项目根绑定；没有 ID 时生成临时会话，不按 cwd/PID/连接合并。显式跳过可在同一对话后续明确切换标准模式；验证与审查只在适用时首次确认，审查注册未知时跳过。

工作包有唯一幂等键、revision/version、范围与状态约束；PASS 子任务报告需要当前修订目录中可验证且哈希未变的证据。VERIFIED 只证明文件与结构满足要求，不证明命令执行真实性，也不是最终验收。选择不测试/只记录方法时测试结果保持 NOT_RUN。资源租约有 owner/token/TTL，过期后旧 token 不能释放新持有人。handoff 只续工作包，不复制会话确认。

路径读写限制在启动时明确配置的本地项目根。证据文件通过目录描述符逐级拒绝符号链接，拒绝父目录导航、绝对路径、非普通文件、空/过大文件与无效测试返回码。SQLite 使用 WAL、事务、busy timeout；伴随文件用一次属性快照检查，避免多进程关闭时 WAL 生命周期误判。

保留 docs-root 顺序与既有记忆/证据/报告路径。MCP 只创建缺失 Markdown 文件，返回主对话同步责任，不覆盖手工编辑；断开时纯 Skill 继续，重新启用需显式核对，无自动导入器。混合安装生成可复制客户端配置，不改写现有配置；失败仍保留纯 Skill。

## 自动测试：PASS

环境：Linux、Python 3.13.3、mcp 2.3.0、mcp-types 2.3.0、Pydantic 2.14.3、pytest 9.2.0、pytest-asyncio 1.4.1.post1。完整依赖见 `mcp_server/requirements-dev.lock`。

实际命令：

```bash
.venv/bin/python -m pytest -q mcp_server/tests \
  --junitxml=docs/agent/evidence/WP-MCP/R1/pytest.xml
```

实测 **80 passed in 8.63s，0 failed，0 errors，0 skipped**。

| 测试文件 | 数量 | 范围 |
| --- | ---: | --- |
| test_p0.py | 36 | 会话隔离/临时 ID、三问顺序、动态推荐、互斥、显式跳过/变更、取消/拒绝/超时/错误、重复/迟到回复、原生优先、能力声明、真实 stdio、并发 claim |
| test_work.py | 29 | 工作包幂等/版本/修订、全部 docs-root、手工文件保留、有效/无效/不足证据、哈希变化、路径与 SQLite 符号链接、锁争用/TTL/旧 token、并发线程/独立进程、DB_BUSY 与重启、handoff、不测试约束 |
| test_install.py | 9 | 纯 Skill/混合安装、重复/备份/卸载、其他标记保留、只读检查、Python 缺失、畸形块、父路径/运行时子路径 symlink、真实安装入口与配置示例 |
| test_protocol.py | 6 | 独立 stdio 子进程新旧协议×有无交互、原始 stdout JSON-RPC 逐帧验证、完整参考客户端工作包/证据/租约/交接闭环 |

完整 pytest 日志及 JUnit XML 留在项目 `docs/agent/evidence/WP-MCP/R1/`，交付时另附测试证据包。早期失败发现并修复了结构化输出、错误包装、迟到取消回复和 SQLite 生命周期竞态；最终通过结果不包含失败/跳过项。

沙箱限制说明：本机沙箱内最小 AnyIO `to_thread.run_sync` 示例无法唤醒事件循环，独立 stdio 测试因此挂起。最终完整测试在获准的本地执行环境运行。没有因此改动 SDK 协议、关闭断言或把挂起记为用户超时。

## 安装包与旧客户端：PASS

- `pip wheel --no-deps ./mcp_server` 成功生成 `scs_mcp-2.0.0-py3-none-any.whl`；安装到隔离目标目录成功，13 个包模块与最终源码逐字节一致。
- 从已安装 wheel 启动独立进程，SDK 2.3.0 auto/legacy 两条链路通过，协议分别 2026-07-28 / 2025-11-25；发现 14 个工具，完成四选一与诊断。回执见 wheel-smoke.json。
- 另建隔离的官方 SDK 1.30.0 环境，真实连接 SDK 2.3.0 服务器的已安装 wheel：协议 2025-11-25。支持表单时完成模式、验证多选、审查三问；未声明支持时返回文本回退并接受明确文本提交。两场景均 PASS；回执见 legacy-sdk1.json。

旧客户端探针可重现：

```bash
python3 -m venv legacy-venv
legacy-venv/bin/python -m pip install 'mcp==1.30.0'
legacy-venv/bin/python scripts/probe_legacy_sdk1.py \
  --server-python /absolute/server-venv/bin/python
```

服务解释器须已安装本包与 mcp 2.3.0，必要时加 `--server-pythonpath /absolute/mcp_server/src`。探针使用临时项目和模拟选择，不调用模型；属于真实 SDK/stdio 兼容检查，**不是图形客户端 UI 验证**。

## 客户端集成与真实 UI

| 客户端 | 实测情况 | UI 结论 |
| --- | --- | --- |
| 官方 SDK 2.3.0 参考客户端 | 内存协议、独立进程、最终 wheel；新旧协议、全工作流通过 | 无图形 UI；UI_NOT_OBSERVABLE |
| 官方 SDK 1.30.0 旧客户端 | 独立环境到最终 wheel；三问/多选及无能力回退通过 | 无图形 UI；NOT_TESTED |
| 当前 Codex 桌面聊天 | 已通过原生提问工具收集本聊天用户模式/审查/执行方式确认；CLI 版本 0.163.0-alpha.10.2，配置格式核对官方文档与 CLI help | 新服务器尚未注册进本聊天，新版 Skill 需重开；真实弹窗视觉观察 NOT_TESTED |
| Claude Code 2.1.1 | 仅本机版本与官方配置文档核对，没有启动模型会话 | NOT_TESTED |
| ZCode | 本机启动器所指 AppImage 不存在，未改动配置或修复客户端 | NOT_TESTED |
| MCP Inspector | 已提供运行方法，未安装/运行 | NOT_TESTED |

当前宿主原生用户回复只能证明原生交互取得确认；不能据此宣称新服务在该宿主成功渲染了表单。UI_NOT_OBSERVABLE 不是 UI 失败，也不是 UI 成功。

## 静态规则检查：PASS

```bash
python3 scripts/check_rules.py
python3 /path/to/skill-creator/scripts/quick_validate.py single-conversation-subagent
bash -n install.sh
git diff --check
```

结果：8 份维护中的指令/文档，旧自动选择规则匹配 0；Skill 校验 `Skill is valid!`；安装器语法及 diff 空白检查通过。检查器额外覆盖此前残留的审查超时默认表述。历史 PNG/GIF 保留为 1.x 参考，HTML 文案已移除冲突；不是新版 UI 截图。

## 安装、配置与回滚

纯 Skill：`bash install.sh --mode skill --with-default-rule`。

混合：`bash install.sh --mode hybrid --with-default-rule --project-root /absolute/project`。只读验证：`bash install.sh --mode hybrid --check`。已准备好的 SDK 解释器可加 `--python /absolute/venv/bin/python`；缺依赖时只在新隔离环境 bootstrap，失败保留此前运行时与 Skill。

混合安装后人工核对并合并生成的 `client-examples.json` 或 `codex-example.toml`，不覆盖已有条目。直接入口为 `python -m scs_mcp --project-root /absolute/project`。Codex 示例：

```toml
[mcp_servers.single-conversation-subagent]
command = "/absolute/venv/bin/python"
args = ["-m", "scs_mcp", "--project-root", "/absolute/project"]
```

其他 JSON stdio 客户端示例及完整命令见 README；ZCode 配置键未验证，不能假定。安装不会自动扩大信任，也不会自动注册到当前聊天。新聊天重新加载规则，第一次四选一重新开始。

卸载：`bash install.sh --uninstall`，自定义过指令文件须加同一 `--instructions-file`。移除客户端中本服务条目，恢复安装前的 `.bak.<时间>.<pid>` Skill、规则和运行时后重开对话。项目 `.scs`、Markdown 和证据由用户保留，卸载不会删除。源码补丁以基线提交为目标；在干净基线目录先 `git apply --check`，应用后可用 `git apply -R` 撤回该补丁，不对含其他改动的目录盲目反向应用。

## 已知限制与未完成的外部验证

- Codex/Claude Code/ZCode 中新服务的实际 UI 集成仍待手工验证；模型可能不调用工具，客户端可能不渲染或自动生成回执。不能保证所有首次弹窗出现。
- SKILL_NOT_LOADED 需宿主加载证据，TOOL_NOT_CALLED 需宿主调用日志；服务无法观察未发生调用。现代客户端回调崩溃于重试前时服务器仍只见 WAITING_USER，宿主须报告错误/转文本，不能猜超时。
- 当前测试 Python 3.13.3；Python 3.11/3.12 的实际运行和 macOS 尚未验证。安全路径实现依赖 POSIX 目录描述符；Windows 原生 MCP 尚未移植，纯 Skill 可手动安装。
- SQLite/租约不是进程级安全沙箱；只适合互信的本地同一用户客户端。资源租约不阻止外部写入，目录锁不自动覆盖子文件。本地只读资源可由知道 ID 的同项目客户端读取，无多用户认证。
- SDK 的 requestState 默认密钥仅当前进程有效；重启后旧令牌需核对并重开，不静默重放。临时会话 ID 也需宿主保留，跨重启不自动辨认。
- 无 Markdown 自动导入器/自动同步覆盖，无跨项目 DB 迁移器、远程 handoff、仪表盘或计费功能。显式核对与手工导入是本版有意采用的边界。
- VERIFIED 不能验证命令执行真实性；主对话需独立核对原始日志。子任务 PASS 与最终接受分开，服务不替主对话给出 ACCEPT。
- 用户本次选择主对话直接实施、不委派、不追加 codex-review，已遵守。发布、提交、推送和真实用户环境安装均未执行。

## 完整修改文件清单

- `.gitignore`
- `AGENTS.snippet.md`
- `CHANGELOG.md`
- `README.md`
- `VERSION`
- `docs/archify/skill-architecture.html`
- `docs/validation.md`
- `docs/单对话subagent-两级协作协议.md`
- `install.sh`
- `mcp_server/pyproject.toml`
- `mcp_server/requirements-dev.lock`
- `mcp_server/src/scs_mcp/__init__.py`
- `mcp_server/src/scs_mcp/__main__.py`
- `mcp_server/src/scs_mcp/diagnostics.py`
- `mcp_server/src/scs_mcp/errors.py`
- `mcp_server/src/scs_mcp/evidence.py`
- `mcp_server/src/scs_mcp/interaction.py`
- `mcp_server/src/scs_mcp/locks.py`
- `mcp_server/src/scs_mcp/models.py`
- `mcp_server/src/scs_mcp/paths.py`
- `mcp_server/src/scs_mcp/server.py`
- `mcp_server/src/scs_mcp/state.py`
- `mcp_server/src/scs_mcp/storage.py`
- `mcp_server/src/scs_mcp/work.py`
- `mcp_server/tests/test_install.py`
- `mcp_server/tests/test_p0.py`
- `mcp_server/tests/test_protocol.py`
- `mcp_server/tests/test_work.py`
- `scripts/check_rules.py`
- `scripts/probe_legacy_sdk1.py`
- `single-conversation-subagent/SKILL.md`
- `single-conversation-subagent/references/mcp.md`
- `single-conversation-subagent/references/protocol.md`
