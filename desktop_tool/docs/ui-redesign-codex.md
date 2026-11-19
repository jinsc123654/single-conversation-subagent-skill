建议采用“统一双行顶栏 + 自适应项目网格 + 首页待办预览”的结构。卡片保持紧凑，页面剩余空间由真实待办或明确的空状态承接。

本次仅静态阅读了指定源码、README 和相关测试引用；未修改文件、运行测试或构建，也未联网。渲染现象以你提供的描述为准。

### 1. 整体布局树

保留系统标题栏。应用内部只保留一套产品标题、导航、摘要和主操作，移除首页、待办页重复的大标题与说明段落。

```text
MainWindow
└─ root                         背景 bg
   ├─ AppHeader                 不滚动，约 110–120 高
   │  ├─ IdentityRow            44 高
   │  │  ├─ SCS 项目管理器
   │  │  ├─ 导航分段按钮：项目总览 / 我的待办 / 项目详情
   │  │  ├─ stretch=1
   │  │  ├─ 全局摘要：项目 7｜进行中 0｜待验收 0｜待办 0
   │  │  └─ 主题选择 / 帮助
   │  └─ ContextToolbar         36–40 高
   │     ├─ 当前页过滤器
   │     ├─ stretch=1
   │     ├─ 搜索框
   │     ├─ 项目选择器          仅待办页需要
   │     └─ + 添加待办          primary
   ├─ pages                     stretch=1，无外层白色 pane
   │  ├─ DashboardPage
   │  │  └─ 一个纵向 ScrollArea
   │  │     └─ scrollBody
   │  │        ├─ 项目网格      按内容高度，不纵向撑大
   │  │        └─ 我的待办预览  少量任务 / 紧凑空状态
   │  ├─ TodoPage
   │  │  └─ 内容状态栈          stretch=1
   │  │     ├─ 全宽任务列表 ScrollArea
   │  │     └─ 居中 EmptyStateCard
   │  └─ 项目详情              保留现有 splitter 和内部 tabs
   └─ 状态提示                  一行，必要时显示
```

建议尺寸：

| 区域 | 边距、间距与伸缩 |
|---|---|
| 页面左右边距 | 24；窄窗口降到 16 |
| 顶栏内边距 | 左右 24、上下 12；两行间距 8 |
| 内容上边距 | 16 |
| 项目网格 | 横纵间距均为 12 |
| 网格与待办预览 | 间距 24 |
| 待办列表 | 行间距 8；横向 Expanding |
| 首页待办预览 | 少量项目时承接剩余高度，建议最低约 210；有内容后按内容增长 |

**摘要必须把标签和数字组成独立单元**：例如“项目 **7**”，组内间距 6，组间距 20；数字 20px/600，标签 12px。不要继续采用两排彼此疏远的数字和标签。全局摘要保持固定口径，tooltip 标明进行中、待验收对应工作包，待办对应未关闭待办。

1500 宽、7 个项目时建议呈现四列、两行，下面接待办预览。末行缺一个位置属于自然留白；底部用真实信息区承接。不要用拉高卡片或额外装饰块填满屏幕。

小窗口时，第一行将摘要移至独立一行，主题/帮助收进菜单；第二行仍保持过滤器、搜索和主按钮同行。断点按控件 `sizeHint()` 总宽判断，避免中文字体变化后挤压。

### 2. 项目卡片重构

当前 `ProjectCard` 只有 `setMinimumSize(270, 168)`，网格又没有收束行高，容易出现卡片纵向撑大。建议改为固定高度、横向伸展。

```text
ProjectCard                      基准高度 176
├─ 名称                     ☆   28
├─ /home/…/project               18
├─ 间距                          8
├─ 进行中    待验收    待办    会话
│     0         0       0       0  40
├─ 间距                          8
└─ 最近活动 · 2 小时前    + 待办  28
```

具体建议：

- 内边距 14，普通间距 4–6；高度不通过 `addStretch()` 分摊。
- 标题 15px/600；路径和最近活动 12px；指标数值 18px/600。
- 四个指标单元等宽，每个单元内部左对齐，取消当前指标末尾的 `addStretch()`。
- 名称右侧省略，路径使用 `Qt.ElideMiddle`；两者均提供完整 tooltip。当前路径已有 tooltip，可直接保留。
- `QLabel` 设置横向 `QSizePolicy.Ignored` 或合适的最小尺寸，避免完整路径的 `sizeHint()` 把列撑宽。
- 置顶使用 28×28 的透明按钮；选中后用 accent 表示状态。
- 添加待办使用“+ 待办”透明小按钮，点击区高 28；主操作的实心 accent 只用于顶栏。
- 读取异常使用固定位置的小状态标记和 tooltip，避免追加一行把单张卡片撑高。
- 卡片空白、名称区域进入详情；路径复制、置顶和添加按钮独立处理事件，避免触发卡片导航。

176 是正常字体下的基准。字体放大时统一提高全部卡片高度，不单独拉伸某一张。

**推荐继续使用 `QGridLayout`，无需自定义 FlowLayout。** 等宽、固定行高的规则网格更适合这里。

按滚动区 **viewport 的可用宽度**计算：

```python
gap = 12
min_card_width = 320
available = viewport_width - left_margin - right_margin
columns = max(1, min(5, (available + gap) // (min_card_width + gap)))
columns = min(columns, max(1, len(visible_cards)))
```

约 1452 可用宽度时为四列，每张约 354；更宽时五列，更窄时三、二、一列。

重排要求：

1. 监听 viewport 的 `QEvent.Resize`，首次显示和数据刷新后也计算。
2. 用单次 `QTimer` 合并连续 resize。
3. **列数不变时不重排、不重建卡片**，由布局自然调整宽度。
4. 列数改变时只取出布局项，将已有卡片重新放入。
5. 清零旧列 stretch/minimumWidth，再为有效列设置等量 stretch。
6. 网格外增加纵向尾部 stretch，固定高度卡片不吸收多余高度。
7. 未满末行保持与前行相同列宽，不把最后三张单独拉宽。

### 3. 待办页与空状态

待办页不再重复标题、说明和大数字区。过滤器与搜索统一放进顶栏第二行：

```text
[未完成] [优先] [进行中] [已完成]   stretch   [搜索待办…] [项目 ▼] [+ 添加待办]
```

“项目”选择器继续作为新建目标，保留现有行为。搜索框建议宽 260–320，允许收缩到约 180。

**有任务时：**

- `TodoCard` 横向填满 viewport，布局不限制最大宽度。
- 左侧完成勾选；中部标题和项目/优先级/截止/状态；右侧编辑按钮。
- 基准最小高度 72，标题换行后允许自然增高。
- `body` stretch=1，勾选和编辑固定尺寸。
- 列表最后保留 stretch，任务行不纵向拉长。
- 当前终态标题硬编码的 `#888888` 改成属性选择器，由 palette 控制。

**没有任务时：**

内容栈切到独立空状态页，不在滚动列表左上角追加一句文字。

```text
              ┌────────────────────────────┐
              │       还没有待办           │
              │ 为项目记录下一步行动，     │
              │ 从一条待办开始。           │
              │       [新建待办]           │
              └────────────────────────────┘
```

卡片宽约 420、最大 480、最小高度约 200，内边距 28；标题 18px/600，说明 13px，说明至按钮间距 20。通过外层上下、左右 stretch 居中，窄窗口允许卡片收缩。

需要区分：

| 状态 | 标题与操作 |
|---|---|
| 全部待办为空 | “还没有待办”；主按钮“新建待办” |
| 搜索或筛选无结果 | “没有匹配的待办”；优先提供“清除筛选”，保留新建入口 |
| 没有可选项目 | “先添加一个项目”；主按钮“添加项目” |

首页待办预览复用同一套任务行和空状态组件；展示少量任务并提供“查看全部”。空状态采用更紧凑版本，不重复放一张巨大说明卡。

### 4. QSS 修改建议

以下是样式示意，颜色全部来自现有 `p`；加入 `build_style()` 的 f-string 时需使用双花括号。布局尺寸以 Qt 布局和尺寸策略为主。

```css
QFrame#appHeader {
    background: {p.surface};
    border: none;
    border-bottom: 1px solid {p.border};
}
QLabel#productTitle {
    font-size: 17px;
    font-weight: 600;
}
QFrame#pageNavigation {
    background: {p.surface_alt};
    border-radius: 18px;
}
QPushButton[role="pageNav"] {
    background: transparent;
    color: {p.text_dim};
    border: 1px solid transparent;
    border-radius: 15px;
    min-height: 0px;
    padding: 6px 14px;
}
QPushButton[role="pageNav"]:checked {
    background: {p.surface};
    color: {p.text};
    border-color: {p.border};
}
QPushButton[role="pageNav"]:hover:!checked {
    color: {p.text};
}
QLabel[role="summaryValue"] {
    font-size: 20px;
    font-weight: 600;
}
QLabel[role="summaryCaption"] {
    color: {p.text_dim};
    font-size: 12px;
}
QPushButton[role="filter"] {
    background: transparent;
    color: {p.text_dim};
    min-height: 0px;
    padding: 7px 12px;
}
QPushButton[role="filter"]:hover {
    background: {p.surface_alt};
}
QPushButton[role="filter"]:checked {
    background: {tint(p.accent, 0.12)};
    color: {p.accent};
}
QPushButton[role="cardAction"] {
    background: transparent;
    color: {p.text_dim};
    border: 1px solid transparent;
    border-radius: 14px;
    min-height: 0px;
    padding: 0px 6px;
}
QPushButton[role="cardAction"]:hover {
    background: {p.surface_alt};
    color: {p.text};
}
QPushButton[role="cardAction"]:checked {
    background: {tint(p.accent, 0.12)};
    color: {p.accent};
}
QFrame#emptyStateCard {
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 12px;
}
QLabel#emptyStateTitle {
    font-size: 18px;
    font-weight: 600;
}
QFrame#todoCard[terminal="true"] QLabel#todoTitle {
    color: {p.muted};
}
QPushButton[role="pageNav"]:focus,
QPushButton[role="filter"]:focus,
QPushButton[role="cardAction"]:focus {
    border: 1px solid {p.accent};
}
```

补充三点：

- 保留现有 `#projectCard`、`#todoCard`、`variant="primary"` 规则，避免再建立一套重复组件样式。
- 小按钮必须覆盖全局 `QPushButton` 的 `min-height:30px` 和 padding；仅调用 `setFlat(True)` 不会覆盖 QSS 灰底。
- 两页滚动容器均设置 `objectName="scrollBody"`，并在 `setWidget()` **之后**调用 `setAutoFillBackground(False)`。viewport 也保持透明，根容器使用 `bg`。

不增加阴影、渐变或整排状态色块；现有 surface、边框和字号层级已经足够。

### 5. `window.py` 的结构与关键片段

**`MainWindow._build()`：统一外壳。**

建议首轮保留 `self.pages` 的 `QTabWidget` 类型和三个索引，隐藏外层 tab bar，使用独立导航按钮驱动它。这样现有 `open_project_from_dashboard()` 的 `setCurrentIndex(2)` 可以继续使用。

```python
self.pages.setObjectName("mainPages")
self.pages.tabBar().hide()

# 三个按钮使用 exclusive QButtonGroup
button.clicked.connect(
    lambda _checked=False, i=index: self.pages.setCurrentIndex(i)
)
self.pages.currentChanged.connect(self.sync_header)
```

仅对外层取消 pane，内部 `self.tabs` 的工作包、执行实例等标签保留：

```css
QTabWidget#mainPages::pane {
    border: none;
    background: transparent;
    top: 0px;
}
```

将主题选择移至全局顶栏，删除详情容器中重复的产品标题行；保留详情操作和信号连接。

**`DashboardPage`：拆分筛选与重排。**

```text
set_data()           更新数据、摘要及卡片内容
render()             计算筛选结果，协调卡片增删
_reflow_cards()      按 viewport 宽度排列现有卡片
_schedule_reflow()   合并 resize 请求
_update_preview()    更新首页待办预览
```

不要继续在 resize 时执行当前“删除全部卡片再创建”的 `render()`。

**`relative_time()`：在展示层兼容 epoch 和 ISO。**

```python
if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
    value = datetime.fromtimestamp(stamp, tz=timezone.utc)
else:
    value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
```

保留后续相对时间逻辑；捕获 `TypeError/ValueError/OverflowError/OSError`，非法值显示“暂无活动”，不回显原始浮点数。`backend.project_catalog()` 已提供 epoch 秒，无需改 backend。

**`ProjectCard`：增加可更新内容和省略逻辑。**

增加 `update_entry()`、`_update_elision()`；尺寸改变时从完整原文重新计算省略文本，不从已省略文本继续处理。置顶改为 checkable，并保留现有三个信号。

**`TodoPage`：内容状态栈。**

增加 `EmptyStateCard` 和 `_update_content_state()`；筛选逻辑继续沿用现有条件。新建按钮仍调用 `request_add()`，编辑、完成仍连接 `edit_board_task()`、`toggle_board_task()`。

顶栏搜索只出现一份，按当前页路由查询；切页保存各页搜索内容。为独立实例化和 offscreen 使用保留 `DashboardPage`、`TodoPage` 的现有接口、信号及 `search`、`summary_labels`、`project_box` 等属性，工具栏允许由 `MainWindow` 挂载到顶栏。

所有尺寸计算使用逻辑像素和 viewport 宽度，不依赖屏幕分辨率、X11 或新图标库。

### 6. 实施优先级

1. **统一应用外壳**：隐藏外层裸 tab，建立双行顶栏，删除重复标题和摘要展示。
2. **修复项目网格**：按 viewport 计算列数、等宽 stretch，固定卡片高度，分离筛选与 resize 重排。
3. **收紧卡片信息**：名称、路径、四指标、活动和操作形成稳定四层结构；补相对时间和省略文本。
4. **完成待办空状态**：内容栈切换，区分首次为空、筛选无结果和无项目。
5. **补齐首页下半区**：用真实待办预览承接空间，复用任务行和紧凑空状态。
6. **统一控件状态**：导航、过滤器、卡片按钮具备一致的正常、悬停、选中和焦点样式。
7. **消除主题断层**：处理滚动背景，移除终态文字硬编码颜色，并保留详情操作与现有 offscreen 接口。

设计结论: ACCEPT