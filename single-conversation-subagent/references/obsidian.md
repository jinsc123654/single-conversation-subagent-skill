# Obsidian 项目视图

只使用当前 MCP 服务，不另装 Obsidian MCP、Dataview 或 Bases。项目 docs-root 作为一个独立库；一个项目中的多次聊天共享该项目视图，各对话的模式选择仍独立。

## 启用与打开

首次绑定对话时，`session_open(project_root=<绝对项目根>, register_obsidian=true)` 初始化库与总览，并发布到 PyQt 的本地项目索引。项目根使用用户明确指定的目录，或宿主提供的当前对话工作目录。选定的 docs-root 固定在项目数据库中，后续对话复用。登记先于执行模式和验证，需有项目元数据与本地索引的写入权限。

如需手动刷新已有视图，可在确认本对话模式和验证范围后调用：

```json
{"session_id":"S-...","initialize_vault":true,"auto_refresh":true}
```

工具名 project_view_refresh。默认 initialize_vault=false，不创建库配置；true 在没有 `.obsidian/` 目录时创建空 app.json，并在项目 `.scs-obsidian/<库ID>/` 准备独立启动 profile/库映射；已有库配置保持原样。auto_refresh 默认 true，开启后的 MCP 工作包创建/更新会自动刷新；设置 false 后只在显式调用时刷新。

返回 vault_root、overview_path、open_uri、updated_files、分组统计与证据问题。vault_root 就是选中的 00_doc/00_docs/docs/doc，不是项目根或 agent。桌面管理器使用 desktop_profile 中的独立启动配置，首次可直接打开；不需要先导入全局库。通用 open_uri 则仍需要用 Obsidian 的“打开文件夹为仓库”登记 vault_root，之后可打开总览。URI 使用编码后的绝对文件路径，不依赖可能重名的 docs 库名。MCP 不写全局 vault 列表、不调用系统启动程序，application_opened 永远 false，vault_registration 为 NOT_OBSERVABLE。

project_view_info(session_id) 是只读工具：不创建文档目录或 .obsidian，检查库配置是否存在、是否已生成视图、数据库快照是否过期、生成段漂移、证据变化、上次刷新是否中断，并返回打开链接。默认无后台监控；阶段结束或需要查看时调用，不为未启用视图的小问题增加工作包。

## 页面布局

```text
<docs-root>/
  .obsidian/app.json               # 可选；只初始化缺失库，不改已有配置
  agent/
    AGENT_PROJECT.md               # 主对话维护的决定与最终验收
    AGENT_COORDINATION.md          # 主对话维护的阶段记忆
    evidence/WP-.../R1/...          # 原始证据
    acceptance/
      00_项目总览.md
      10_需要关注.md
      20_进行中.md
      25_待验收.md
      30_已完成子任务.md
      50_未分类.md
      90_最近修订.md
      95_全部工作包.md
      99_视图说明.md
      work-packages/WP-....md
      revisions/WP-...-R1.md
      .scs-view.json                # 本生成器的文件管理与恢复清单
```

总览只链接状态分组、最近修订、说明和源记忆，全部工作包索引由说明页进入，避免所有任务都直连总览。工作包页链接各修订及原始证据；记忆来源链接源文件，不假定手工记录里一定有可用标题锚点。缺失的历史快照不虚构。旧 docs-root 的来源留在原处，显示库外项目相对路径，不迁移或链接逃出当前库。

状态来自 SQLite 的工作包和子任务报告。证据仍有效的 PASS 子任务列入“已完成子任务”；REVIEW_READY 工作包列入“待验收”。服务不推断最终 ACCEPT、已完成整个项目或已接受风险；主对话的最终决定仍查 AGENT_PROJECT.md。手工源记忆不被自动解析/导入数据库。

## 批注、漂移与恢复

每页末尾的两条独立标记之间可写批注：

```markdown
<!-- manual:begin -->
我的批注或待办。
<!-- manual:end -->
```

刷新原样保留该区域。不要新增同名标记、删/颠倒标记，或编辑生成区域来修改真实任务状态。已有同名文件但不在生成器管理清单内时返回 VIEW_CONFLICT；旧项目的其他生成器不会被自动接管。

所有目标先检查，发现已有冲突时不会开始更新其他视图页面。每个文件用内容前置检查和原子替换；同服务参与者通过 SQLite 事务串行。外部编辑器不受硬锁限制，极窄的外部写入竞争无法保证完全排除。批注在刷新期间变化会尽量检测并停止，重试前读取最新内容。

刷新在写页面前保存旧/新内容归属，进程中断后 project_view_info 标 refresh_incomplete，下次 refresh 可识别自身部分输出继续；不删除已有页面、用户数据或孤立历史文件。生成段发生手改则先人工核对，不能通过删清单/强制覆盖来绕过保护。限 2000 个生成页面，单文件/清单最大 2 MB，超过返回 VIEW_LIMIT，不无限扩展上下文或输出。

自动刷新失败时工作包已保存，响应仍 status=OK、version 已递增，额外 view_update.status=ERROR、work_package_saved=true。此时仅处理视图冲突并重新 refresh，不重交原工作包变更。证据后来变化时 info 标 evidence_stale；刷新后页面进入“需要关注”，不继续列作已完成。历史页保留当时报告，并提示核对当前证据。

## 升级与回退

2.1 迁移 SQLite schema 1 → 2，保留原会话、问题、工作包和证据，新增按工作包/修订保存的最新快照。旧版已经丢失的早期结构化修订无法恢复，迁移只回填实际当前修订，注明历史缺失。升级现有服务前停止写入并备份 .scs 状态；已有 Markdown 无需迁移。

2.0 服务器会拒绝 schema 2；回退到旧程序时同时恢复升级前的状态备份，避免覆盖新状态。Obsidian 视图和批注可保留，但旧程序不会自动刷新。关闭视图只需 refresh(auto_refresh=false)，不删除库配置或任何页面。卸载 MCP 同样不删除项目文档。

纯 Skill 模式仍可用 Obsidian 直接浏览 Markdown；数据库恢复前由主对话核对手工变更。此功能不承诺当前客户端一定能唤起 Obsidian，也不宣称 .obsidian 存在就已注册；图形界面结果必须单独观察验证。

## 独立启动配置（2.2）

这不是 `.obs` 文件。Obsidian 的库设置是 `.obsidian` 目录，启动配置是独立 profile 内的 obsidian.json。配置归属标记 `.scs-profile.json` 绑定项目与库，未受管/冲突/符号链接会拒绝覆盖。独立 profile 不共享全局库登记和登录，Obsidian 本身仍负责运行及保存工作区。MCP 只生成文件、返回 desktop_profile，不执行程序。此接口按本机 Obsidian 1.14.4 验证，非通用 URI 自动导库承诺。
