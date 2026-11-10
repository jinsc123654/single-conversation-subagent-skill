# SCS 项目管理器（可选）

配套查看本地 single-conversation-subagent MCP 项目。参照本机 `pyqt工具/example_tool` 的 PyQt5、GNOME 风格和异步取数约定；`theme.py` 是参考主题的原样副本，身份配置独立。使用系统标题栏方便平铺、多屏和缩放。参考目录未修改。

## 直接运行，不必安装

已有 Python 3.11+ 和 PyQt5 时，在仓库根运行：

```bash
bash desktop_tool/run.sh
# 带入项目及已装 MCP SDK 2.3 的 Python：
bash desktop_tool/run.sh --project /absolute/project --mcp-python /absolute/mcp-venv/bin/python
bash desktop_tool/build.sh check
```

可从文件管理器运行 `run.sh`；也可以在终端启动。仅查看已有数据库不需要安装 MCP SDK。连接自检和刷新视图需要 **单独的 MCP Python 环境（SDK 2.3 / scs-mcp 2.1）**，默认尝试仓库 `.venv/bin/python`，找不到时尝试管理器自己的 Python；可在界面选择。

没有 PyQt5 时，按需在**独立环境**安装，不属于 MCP 安装步骤：

```bash
python3 -m venv /absolute/optional-gui-venv
/absolute/optional-gui-venv/bin/pip install -r desktop_tool/requirements.txt
SCS_GUI_PYTHON=/absolute/optional-gui-venv/bin/python bash desktop_tool/run.sh
```

不会自动运行 pip/apt、安装菜单条目、桌面启动器或图标；`install.sh --mode hybrid` 不引用本目录。缺少 PyQt5 不影响 MCP 或纯 Skill。此版本提供源码运行包，不要求 PyInstaller、系统服务或社区插件。

## 使用

1. **添加项目**：选择项目根。**发现项目**：在你指定的目录及下两层查找 `.scs/state.sqlite3`，最多遍历 2000 个目录；不会扫描整个主目录。列表最多保存 500 个项目。尚未使用 MCP 的目录也可手动添加，显示“未初始化”。
2. **项目栏 / 目录**：项目可展开、收起，子项显示最近最多 8 个真实工作包；折叠状态保存在本工具配置。按最后 MCP 事件/工作包修订时间，或用户显式打开时间，从新到旧自动排序。后台读取不会更新活动时间。选中工作包可直达工作包页，排序保留当前选择与收起状态。浏览文件和文件夹；双击/打开按钮只打开包含目录，不执行文件。复制路径后可自行管理其他项目文件。移出列表仅移除条目，项目文件保留。
3. **Obsidian（2.2 首次可直接打开）**：项目按钮定位 docs-root 的总览；目录按钮识别选中位置附近的库（包括嵌套项目），Markdown 文件可直达笔记。首次会自动准备 `.obsidian` 和项目 `.scs-obsidian/<库ID>/obsidian.json` 启动配置，并通过 `obsidian --user-data-dir=<配置目录> <打开链接>` 打开，无需先手工登记。相同 `docs` 名称通过唯一库 ID 区分。使用本机 Obsidian 原程序，既有全局库列表不动。此路径在本机 Linux / Obsidian 1.14.4 已实测；其他安装方式需保证 `obsidian` 程序位于 PATH。
4. **状态与工作包**：查看本地数据、会话、工作包、修订、生成视图、最近 40 条事件。工作包最多展示最近 500 个，会话最多 200 个；汇总是实际总数。主对话验收与子任务通过分别保留原语义。
5. **连接自检**：主动启动一次短期 stdio 客户端/服务，读取诊断、工具清单和所选会话的视图有效性。完成后检查实例退出。它证明这次检查能连接，不代表 Codex/其他宿主正在连接；宿主状态始终“不可观测”。不启动常驻服务、不停止/重启宿主进程、不读取各客户端配置或凭据。只有已升级 schema 2 的库允许连接检查，避免状态按钮偷偷迁移旧库。
6. **刷新 / 初始化视图**：选择已有、完成必要确认的可写会话，确认具体目录后调用原 MCP 工具；不会替会话选择工作模式或生成任务结果。保留 `manual` 批注、冲突停止；缺少库配置时创建空配置；随后工作包更新可以自动刷新。未确认会话/只调查模式不能写。
7. **归档整个视图**：明确确认后，把 `docs-root/agent/acceptance` 整体移动到同级 `view-archives/UTC时间-编号`，保留所有批注及额外笔记，自动刷新暂停。SQLite 写事务避免与本服务的刷新交错；外部编辑器不受这把锁约束，请先关闭编辑窗口。原始数据库、记忆、证据与 `.obsidian` 不动。界面没有永久删除功能。

归档恢复：暂停 MCP 写入并关闭编辑器，确认 `acceptance` 不存在，再将整个归档目录移回该名称，执行连接检查。若已生成新视图，先归档新视图，勿覆盖合并；人工核对两份批注。也可以保留归档，直接刷新生成新的视图。

## 存储与退出

本工具仅将项目列表和解释器选择保存在 `${XDG_CONFIG_HOME:-~/.config}/scs-manager/projects.json`，不写客户端 MCP 配置、不注册 Obsidian 全局库。可用 `--config /absolute/path.json` 指定独立文件。列表在添加/移出、设置解释器、展开/收起或显式打开时保存；`--project` 是本次启动带入项。

自动读取每 8 秒触发，隐藏/最小化暂停；单任务在途，后台进程取数，不阻塞界面，不重建未变化的状态。超时/关闭只回收本窗口拥有的进程组。若刷新/归档中途退出，操作可能已完成，应重新读取状态，不要盲目重试。SQLite 只读查看不修改业务数据或迁移；SQLite 自身可能维护 WAL/SHM 伴随文件。

不再使用时关闭窗口并删除工具目录；需要清空列表时单独删除本工具的 `projects.json`（帮助/关于会显示精确路径）。无需卸载系统组件。关闭窗口不会停止宿主中的 MCP。

## 验证

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=desktop_tool/src python3 -m unittest discover -s desktop_tool/tests -v
# 真连接集成测试：仅在临时项目写数据，不打开外部应用。
QT_QPA_PLATFORM=offscreen PYTHONPATH=desktop_tool/src python3 desktop_tool/tests/probe_live.py \
  --server-python /absolute/mcp-venv/bin/python --output /tmp/scs-gui-integration.json
```

覆盖数据库不迁移、路径/符号链接、发现上限、库定位/编码、归档/恢复、并发锁、真实异步取数、失败/关闭回收、深浅主题与小窗口；另跑真实 GUI→worker→官方 SDK→stdio MCP 的自检、刷新、批注保留、归档后生成闭环。离屏渲染不代表已观察到真实多屏或外部应用打开。

Obsidian 跳转参数依据官方 [URI 文档](https://obsidian.md/help/uri)，首次库打开参见 [Manage vaults](https://obsidian.md/help/Files%20and%20folders/Manage%20vaults)。

独立 Obsidian 启动配置是应用运行数据，会包含该实例的缓存；不属于笔记或证据。关闭对应 Obsidian 后可自行备份/清理该项目 `.scs-obsidian`，下次点击会重新准备；不自动删除它。项目 `.obsidian` 内原有设置保持，MCP 不复制个人配置、插件、登录信息，也不自动启动 Obsidian。
