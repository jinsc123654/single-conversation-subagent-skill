# 2.2.0 验收记录

2026-11-11，基线 a1ca6dd，分支 feat/mcp-hybrid。本记录生成于提交前；用户现已授权将 MCP 与可选 PyQt 管理器同仓库提交推送，最终提交与远端分支以 Git 记录为准。用户沿用长任务、主对话直接、不委派、不追加独立审查，并授权更新已安装管理器。

本轮完成：项目栏可折叠并持久记忆、显示真实工作包子项、按最后 MCP 事件/修订或显式打开时间自动排序；读取不制造活动。每8秒可见时更新，排序保留选择、滚动与折叠状态。

Obsidian 首次一键打开改为项目独立 profile。MCP initialize_vault=true 自动准备 `.obsidian` 及 `.scs-obsidian/<库ID>/obsidian.json`；PyQt 点击时也可以独立准备，调用本机 Obsidian 加 --user-data-dir 打开。未修改全局库登记，不伪造 `.obs` 扩展名，不覆盖原有库配置。MCP 本身不启动程序，仍1服务16工具、schema2。

自动验证：121 项 MCP pytest 全部通过；30 项桌面 unittest 全部通过；真实桌面→worker→SDK→stdio 六项集成闭环通过；隔离安装的2.2 wheel新旧协议及SDK1.30客户端通过。证据在 agent/evidence/WP-MCP/R5。深浅色、800×600及全尺寸离屏截图已核对；树显示展开与收起项目。

首次独立 profile 使用本机 Obsidian 1.14.4 创建一次性库，实际观察到 scs-direct-probe 笔记窗口标题。已从安装版真实按钮打开本项目总览，X11窗口标题确认“00_项目总览 - docs - Obsidian 1.15.0”，管理器窗口也存在；全局 obsidian.json 前后SHA256相同。安装版按钮返回值兼容已覆盖布尔/元组两种PyQt形式。未声称所有平台/Obsidian版本已通过。普通全局open_uri仍要求已登记库，桌面按钮采用独立profile消除该限制。未使用远程调试、未关闭沙箱、未更改安全/信任设置。

2.0与2.1历史验收分别见 validation-2.0.md / validation-2.1.md；R4已经按用户要求安装可选管理器，早期“未安装”是当时事实。当前管理器安装不是 MCP 的强制依赖。
