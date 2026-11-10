"""主题：GNOME（libadwaita）风格调色板 + 样式表 + 桌面明暗自动跟随。

**这份文件在 pyqt工具/ 下五个地方逐字节一致**：四个工程（u8console /
monitor_brightness / synctool / odconsole）+ 样板 example_tool（新工程从样板
复制出来就自带这套主题）。改配色请改这里的调色板，**不要在窗口或组件里写死
颜色** —— 那样浅色模式下必然出错。改完在 synctool 里跑 `bash sync-theme.sh`
（只同步本文件）或 `bash sync-shared.sh`（同步全部共享模块），脚本会 `cmp`
逐个核对。

## 风格规范（对齐 GNOME libadwaita）

**色板**：同一套 token 名，明暗各一份，取值对齐 libadwaita 的命名颜色，但
**层次差比 adw 故意拉大一档**（实测完全照抄 adw 的层次差，深色下卡片和窗底
会糊成一片、交界看不出来；nemo"侧栏暗、内容亮、边界利落"就是这么来的）：

- `bg` = window 底，也是无边框外壳 appCard 的"窗框/chrome"色（深 #1e1e1e /
  浅 #f2f2f4，纯色，**不做渐变**）—— 内容卡片必须比它亮一整档才有交界
- `surface` = 卡片 / 菜单 / 标签页面（深 #323236 / 浅 #ffffff，浮在 chrome 上）
- `surface_alt` = 控件底、悬停底（按钮就是一块无描边的 `surface_alt` 圆片）
- `view` = 输入类/内容区（输入框、日志、列表、表格；比窗底更暗的内凹面）
- `border` 1px 描边（要看得清，别调成和 surface 融为一体）、`border_strong` 悬停描边/滚动条
- `accent` = GNOME 蓝（深 #3584e4 / 浅 #1c71d8）；语义色 `green/red/amber/violet`
  对齐 GNOME HIG 色阶，半透明浅底一律走 `tint()`

**层次**：底 `bg` → 卡片 `surface` → 控件 `surface_alt`，共 3 层；输入/内容区
用 `view`。不要为区分再引入第 4 层明度，靠 1px `border` 和留白分层。

**形状**：卡片 / 菜单 / GroupBox 圆角 12px（libadwaita 的 card & popover）；
按钮是**胶囊**（radius 16px，QSS 里取经验值，够到半高即成胶囊）；输入框、
列表、标签页 8px；复选框是**圆的**、单选框是"圆环 + 中点"—— 都照 GNOME。

**按钮**：无边框平色（描边是 GTK 时代的画法）；`primary` = accent 实心胶囊，
`danger` = red 实心胶囊（destructive-action），`flat` = 透明、悬停才有底，
`go` = green 实心，`step` / `seg` 是紧凑控件照旧。

**间距**：窗口外边距 18px；卡片内边距 16/14px；控件间距 8~10px。
宁可留白多一点也不要把元素挤在一起。

**字号**：正文 13px，次要说明 12px，卡片标题 15px，窗口标题 17px，主数值 26px。
字重只用 400 / 600 两档，不用 700 及以上（Qt 渲染粗体发糊）。

**状态色语义**（不要乱用）：
`green` 可用/成功 · `amber` 需要注意 · `red` 错误/失败 · `accent` 进行中/主操作 ·
`muted` 中性说明。

**组件里只用属性，不写字面颜色**：
`[tone="ok|warn|error|info|muted"]` 文字色调 ·
`[strong="true"]` 加粗说明 ·
`[variant="primary|go|danger|flat|step|seg"]` 按钮形态 ·
`[backend="ddc|backlight|soft|dead"]` 后端胶囊 ·
`[dot="ok|busy|warn|error|off"]` 状态圆点 ·
`[level="error|warn|info"]` 诊断卡片 ·
`[dead="true"]` 掉线的屏幕卡片 ·
`[seg="first|middle|last"]` 分段控件首尾圆角。

自绘控件（Sparkline 这类）用 `tone("accent")` 在 paintEvent 里现查颜色，别把
颜色存进实例 —— 否则换主题时它会留在旧配色上。唯二的例外是 QToolTip（GNOME
两个明暗主题下 tooltip 都是深底白字，写死）和窗口投影（shell.py 手绘的黑）。

## ⚠️ 两个 Qt 的坑（都踩过，别再踩）

1. **QSS 的 `rgba()` 不认小数 alpha**。写 `rgba(91,141,239,0.09)` 会让整条声明
   解析失败，控件退回 Qt 默认调色板（浅灰）—— 深色主题下就是一块刺眼的白。
   必须换算成 0~255 的整数，统一走 `tint()`。
2. **`QScrollArea.setWidget()` 会把内容容器的 autoFillBackground 打开**，容器于是
   用 Qt 默认调色板的 Window 色（#efefef）铺满整片滚动区，深色主题下卡片底下
   透出一大块白。要在 setWidget() **之后** 调 `container.setAutoFillBackground(False)`，
   并给容器起名 `scrollBody`（下面有对应的 transparent 规则）。

## 明暗跟随

探测顺序（先命中的算）：

1. 显式指定：命令行 `--theme dark|light`、环境变量
   `<APP_ID 大写、- 换成 _>_THEME` / `QT_THEME`、代码里 `set_forced()`
2. XDG 桌面门户 `org.freedesktop.portal.Settings` 的
   `org.freedesktop.appearance color-scheme`（GNOME/KDE 等都实现，比 gsettings 通用）
3. GNOME 的 `org.gnome.desktop.interface color-scheme`（`prefer-dark` → 深色）
4. GTK 主题名里带 `dark`
5. Qt 调色板窗口色的明度（**排在系统探测后面**：没接平台主题时 Qt 用默认浅色调色板，
   本机实测会误报 light）
6. 兜底深色

运行中跟着变：ThemeWatcher 优先订阅门户的 **SettingChanged 信号**（桌面切明暗
毫秒级到达，不用轮询）；订阅不上就退回每 4 秒问一次 gsettings；同时接 Qt 的
paletteChanged。变了就重装样式表 + 调色板并对所有控件 repolish —— 颜色全在
QSS 里，组件不存颜色，所以**不用重建界面**。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication, QWidget

# ⚠️ 必须**导入期**就拿到 QDBusVariant：下面 ThemeWatcher 的槽装饰器
# @pyqtSlot(str, str, "QDBusVariant") 里的类型串在类定义时解析，QtDBus 还没进
# sys.modules 的话 theme.py 一被 import 就 TypeError（踩过：--check 这条不开
# GUI 的路径直接崩）。没编 QtDBus 的极简 PyQt5 则退回轮询跟随。
try:
    from PyQt5.QtDBus import QDBusVariant
except ImportError:                    # pragma: no cover
    QDBusVariant = None

from .config import APP_ID

# ── 调色板 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Palette:
    """一套完整的颜色 token。字段含义见模块开头的风格规范。"""

    scheme: str          # "dark" / "light"，仅用于展示
    bg: str              # 窗口底（纯色）
    surface: str         # 卡片
    surface_alt: str     # 控件 / 悬停
    view: str            # 输入框 / 日志 / 列表 / 表格的内容底
    border: str          # 1px 描边
    border_strong: str   # 悬停描边 / 滚动条
    text: str
    text_dim: str        # 次要文字
    muted: str           # 中性说明
    knob: str            # 滑块手柄填充
    knob_border: str     # 滑块手柄描边
    accent: str
    green: str
    red: str
    amber: str
    violet: str
    on_accent: str       # 铺在 accent 上的文字色
    on_green: str        # 铺在 green 上的文字色


# 取值对齐 libadwaita（GNOME 42+）的命名颜色；层次差故意比 adw 拉大一档 ——
# 窗底比卡片暗得多、描边看得清，否则深色下卡片和窗底糊成一片（nemo 那种
# "侧栏暗、内容亮、边界利落"的观感就是这么来的）
DARK = Palette(
    scheme="dark",
    bg="#1e1e1e",            # window 底（压暗，把卡片衬出来）
    surface="#323236",       # card / headerbar（比窗底亮一整档）
    surface_alt="#3d3d43",
    view="#19191c",          # view 底（比窗底更暗的内凹面）
    border="#45454c",
    border_strong="#5d5d64",
    text="#ededed",
    text_dim="#b6b6b6",
    muted="#8f8f8f",
    knob="#ffffff",
    knob_border="#1b1b1b",
    accent="#3584e4",
    green="#33d17a",
    red="#e01b24",
    amber="#f5c211",
    violet="#c061cb",
    on_accent="#ffffff",
    on_green="#06301a",
)

LIGHT = Palette(
    scheme="light",
    bg="#f2f2f4",            # window 底（灰一档，白卡片才有边界）
    surface="#ffffff",       # card
    surface_alt="#eaeaee",
    view="#ffffff",          # view_bg_color
    border="#d8d9df",
    border_strong="#bcbdc5",
    text="#32323a",
    text_dim="#63646c",
    muted="#8a8b94",
    knob="#ffffff",
    knob_border="#b8b9c0",
    accent="#1c71d8",
    green="#26a269",
    red="#c01c28",
    amber="#a86f08",
    violet="#9141ac",
    on_accent="#ffffff",
    on_green="#ffffff",
)

_current: Palette = DARK

_ui_forced: Optional[str] = None       # 界面里临时钉死的主题（见 set_forced）

SCHEME_ENV = f"{APP_ID.upper().replace('-', '_')}_THEME"

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SETTINGS = "org.freedesktop.portal.Settings"
PORTAL_NAMESPACE = "org.freedesktop.appearance"
PORTAL_KEY = "color-scheme"


def current() -> Palette:
    """当前生效的调色板。"""
    return _current


def tone(name: str) -> str:
    """按名字取当前调色板里的颜色，给自绘控件用（paintEvent 里现查）。"""
    value = getattr(_current, name, "")
    if isinstance(value, str) and value.startswith("#"):
        return value
    return _current.accent


def _rgb(color: str) -> Tuple[int, int, int]:
    value = color.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def tint(color: str, alpha: float) -> str:
    """同一个颜色的半透明版本，用于胶囊底、诊断卡片底、悬停底。

    ⚠️ alpha 必须换算成 0~255 的**整数**。Qt 的 QSS `rgba()` 只认整数或百分比，
    写成 `rgba(...,0.09)` 这种小数会让**整条声明解析失败**，控件随即退回 Qt 默认
    调色板（浅灰）—— 在深色主题下就是一块刺眼的白。这个坑踩过一次：诊断卡片和
    标签胶囊全变成了浅灰白，而卡片本身颜色正常，看着像"主题混搭"。
    """
    red, green, blue = _rgb(color)
    level = max(0, min(255, int(round(alpha * 255))))
    return f"rgba({red}, {green}, {blue}, {level})"


def _mix(a: str, b: str, f: float) -> str:
    """把颜色 a 向颜色 b 混 f（0~1），算悬停/按下态用 —— 不新增调色板字段。"""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return "#{:02x}{:02x}{:02x}".format(
        round(ra + (rb - ra) * f), round(ga + (gb - ga) * f),
        round(ba + (bb - ba) * f))


def _shift(color: str, palette: Palette, f: float) -> str:
    """悬停/按下时底色的变化方向跟随明暗：深色变亮、浅色变暗（GTK 的画法）。"""
    if palette.scheme == "dark":
        return _mix(color, "#ffffff", f)
    return _mix(color, "#000000", f)


# ── 明暗探测 ────────────────────────────────────────────────

def _qt_scheme() -> Optional[str]:
    """问 Qt 自己的调色板。没接平台主题时不准，所以排在系统探测之后。"""
    app = QApplication.instance()
    if app is None:
        return None
    window = app.palette().color(QPalette.Window)
    if not window.isValid():
        return None
    return "dark" if window.lightness() < 128 else "light"


def _gsettings(key: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", key],
            capture_output=True, text=True, timeout=2.0, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip().strip("'\"").lower()


def _gnome_scheme() -> Optional[str]:
    value = _gsettings("color-scheme")
    if value in ("prefer-dark", "dark"):
        return "dark"
    if value in ("prefer-light", "light"):
        return "light"
    return None


def _gnome_theme_name() -> Optional[str]:
    """GTK 主题名里带 dark / light 也算线索（Yaru-dark 这类）。"""
    name = _gsettings("gtk-theme")
    if not name:
        return None
    if "dark" in name:
        return "dark"
    if "light" in name:
        return "light"
    return None


def _portal_scheme() -> Optional[str]:
    """XDG 桌面门户的 color-scheme：0=无偏好 1=深 2=浅。

    比单问 gsettings 通用（KDE / 其它实现门户的桌面都答得上）；QtDBus 不可用
    或门户没起就返回 None，让后面的探测接手。
    """
    try:
        from PyQt5.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
    except ImportError:
        return None
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    iface = QDBusInterface(PORTAL_SERVICE, PORTAL_PATH, PORTAL_SETTINGS, bus)
    if not iface.isValid():
        return None
    reply = iface.call("Read", PORTAL_NAMESPACE, PORTAL_KEY)
    if reply.type() != QDBusMessage.ReplyMessage or not reply.arguments():
        return None
    try:
        code = int(reply.arguments()[0])
    except (TypeError, ValueError):
        return None
    if code == 1:
        return "dark"
    if code == 2:
        return "light"
    return None


def forced_scheme() -> Optional[str]:
    """谁把主题钉死了（环境变量 / set_forced）—— 钉死了就别再自动跟随桌面。"""
    for value in (os.environ.get(SCHEME_ENV), os.environ.get("QT_THEME"),
                  _ui_forced):
        if value in ("dark", "light"):
            return value
    return None


def set_forced(scheme: Optional[str]) -> None:
    """界面里强制主题：传 "dark" / "light" 钉死，传 None 恢复跟随桌面。"""
    global _ui_forced
    _ui_forced = scheme if scheme in ("dark", "light") else None


def detect_scheme(force: Optional[str] = None) -> str:
    """探测桌面是深色还是浅色，返回 "dark" / "light"。顺序见模块开头。"""
    for value in (force, os.environ.get(SCHEME_ENV), os.environ.get("QT_THEME"),
                  _ui_forced):
        if value in ("dark", "light"):
            return value

    portal = _portal_scheme()
    if portal is not None:
        return portal

    # `gsettings get ... color-scheme` 没显式设过时回 'default'，
    # 这时才轮到 GTK 主题名（Yaru-dark 这类）来定
    explicit = _gnome_scheme()
    if explicit is not None:
        return explicit

    themed = _gnome_theme_name()
    if themed is not None:
        return themed

    from_qt = _qt_scheme()
    if from_qt is not None:
        return from_qt

    return "dark"


def palette_for(scheme: str) -> Palette:
    return LIGHT if scheme == "light" else DARK


def resolve(force: Optional[str] = None) -> Tuple[str, Palette]:
    scheme = detect_scheme(force)
    return scheme, palette_for(scheme)


# ── 样式表 ──────────────────────────────────────────────────

def build_style(palette: Optional[Palette] = None) -> str:
    """按调色板生成整份 QSS。

    颜色全在这里，组件只用属性选择器 —— 换主题只要换样式表 + repolish，
    不用重建界面。悬停/按下色由 _shift() 现算，不进调色板。
    """
    p = palette or _current
    hover_alt = _shift(p.surface_alt, p, 0.08)
    press_alt = _shift(p.surface_alt, p, 0.16)
    hover_accent = _shift(p.accent, p, 0.10)
    press_accent = _shift(p.accent, p, 0.20)
    hover_go = _shift(p.green, p, 0.10)
    hover_danger = _shift(p.red, p, 0.10)
    return f"""
* {{
    font-family: "Adwaita Sans", "Cantarell", "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", sans-serif;
    font-size: 13px;
    color: {p.text};
}}
QWidget#root {{
    background: {p.bg};
}}

QFrame#card, QFrame#screen {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 12px;
}}
QFrame#metric {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
}}
QFrame#screen:hover {{ border-color: {p.border_strong}; }}
QFrame#screen[dead="true"] {{ border-color: {tint(p.red, 0.5)}; }}
QFrame#hairline {{ background: {p.border}; border: none; max-height: 1px; }}
/* 滚动区的内容容器：QScrollArea.setWidget() 会给它开 autoFillBackground，
   不显式声明透明的话它会用 Qt 默认调色板（浅灰）铺满，深色主题下透出一大片白 */
QWidget#scrollBody {{ background: transparent; }}

QFrame#issue {{
    background: {tint(p.amber, 0.10)};
    border: 1px solid {tint(p.amber, 0.32)};
    border-radius: 12px;
}}
QFrame#issue[level="error"] {{
    background: {tint(p.red, 0.10)};
    border-color: {tint(p.red, 0.36)};
}}
QFrame#issue[level="info"] {{
    background: {tint(p.accent, 0.09)};
    border-color: {tint(p.accent, 0.30)};
}}
/* 同步任务卡：与其他内容卡片同款浮在 chrome 上，预检通过描边转绿、被拦下转红 */
QFrame#task {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
QFrame#task:hover {{ border-color: {p.border_strong}; }}
QFrame#task[state="ok"]   {{ border-color: {tint(p.green, 0.45)}; }}
QFrame#task[state="warn"] {{ border-color: {tint(p.amber, 0.45)}; }}
QFrame#task[state="error"] {{ border-color: {tint(p.red, 0.50)}; }}
QFrame#task[state="off"]  {{ border-color: {p.border}; }}

QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 17px; font-weight: 600; }}
QLabel#stateText {{ font-size: 22px; font-weight: 600; }}
QLabel#screenName {{ font-size: 15px; font-weight: 600; }}
QLabel#bigValue {{ font-size: 26px; font-weight: 600; }}
QLabel#cardTitle {{ color: {p.muted}; font-size: 12px; font-weight: 600; }}
QLabel#metricValue {{ font-size: 17px; font-weight: 600; }}
QLabel#metricLabel {{ color: {p.muted}; font-size: 11px; }}
QLabel#muted {{ color: {p.muted}; font-size: 12px; }}
/* 样板（example_tool）概览页的两个说明文字形态 */
QLabel#info {{
    font-family: "Adwaita Mono", "JetBrains Mono", "Noto Sans Mono CJK SC", monospace;
    font-size: 12px; color: {p.text_dim};
}}
QLabel#hint {{ color: {p.muted}; font-size: 12px; }}

/* 状态圆点：尺寸固定，颜色按语义走 */
QLabel#dot {{ border-radius: 7px; background: {p.muted}; }}
QLabel#dot[dot="ok"]    {{ background: {p.green}; }}
QLabel#dot[dot="busy"]  {{ background: {p.accent}; }}
QLabel#dot[dot="warn"]  {{ background: {p.amber}; }}
QLabel#dot[dot="error"] {{ background: {p.red}; }}
QLabel#dot[dot="off"]   {{ background: {p.muted}; }}

/* 文字色调：组件只设属性，颜色在这里定 */
QLabel[tone="muted"] {{ color: {p.muted}; font-size: 12px; }}
QLabel[tone="ok"]    {{ color: {p.green}; font-size: 12px; }}
QLabel[tone="warn"]  {{ color: {p.amber}; font-size: 12px; }}
QLabel[tone="error"] {{ color: {p.red};   font-size: 12px; }}
QLabel[tone="info"]  {{ color: {p.accent}; font-size: 12px; }}
/* 放在 tone 之后：同优先级下后者生效 —— 加粗的说明文字统一 13px */
QLabel[strong="true"] {{ font-weight: 600; font-size: 13px; }}

/* 后端标签：浅底胶囊 + 同色文字 */
QLabel#tag {{
    font-size: 11px; font-weight: 600;
    border: none; border-radius: 10px; padding: 3px 10px;
    background: {p.surface_alt}; color: {p.muted};
}}
QLabel#tag[backend="ddc"]       {{ background: {tint(p.green, 0.14)};  color: {p.green}; }}
QLabel#tag[backend="backlight"] {{ background: {tint(p.accent, 0.14)}; color: {p.accent}; }}
QLabel#tag[backend="soft"]      {{ background: {tint(p.amber, 0.14)};  color: {p.amber}; }}
QLabel#tag[backend="dead"]      {{ background: {tint(p.red, 0.14)};    color: {p.red}; }}
/* 同步任务卡上的徽章：说的是"能不能同步"（sync-yes 绿 / sync-no 灰）；
   模式（镜像/根目录…）改成卡片上的小灰字，不再用徽章 */
QLabel#tag[backend="sync-yes"] {{ background: {tint(p.green, 0.14)}; color: {p.green}; }}
QLabel#tag[backend="sync-no"]  {{ background: {p.surface_alt};       color: {p.muted}; }}
/* 兼容旧写法（root/mirror/normal 三个 backend 不再用于同步卡） */
QLabel#tag[backend="root"]   {{ background: {tint(p.red, 0.14)};    color: {p.red}; }}
QLabel#tag[backend="mirror"] {{ background: {tint(p.amber, 0.14)};  color: {p.amber}; }}
QLabel#tag[backend="normal"] {{ background: {tint(p.accent, 0.14)}; color: {p.accent}; }}

/* 按钮：GNOME 的平色胶囊 —— 无描边，靠底色和悬停变化分层 */
QPushButton {{
    background: {p.surface_alt};
    border: none;
    border-radius: 16px;
    padding: 7px 16px;
    color: {p.text};
    min-height: 30px;
}}
QPushButton:hover {{ background: {hover_alt}; }}
QPushButton:pressed {{ background: {press_alt}; }}
QPushButton:disabled {{ background: {p.surface_alt}; color: {p.muted}; }}

QPushButton[variant="primary"] {{
    background: {p.accent}; border: none; color: {p.on_accent}; font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {hover_accent}; }}
QPushButton[variant="primary"]:pressed {{ background: {press_accent}; }}
QPushButton[variant="primary"]:disabled {{ background: {tint(p.accent, 0.28)}; color: {tint(p.on_accent, 0.55)}; }}

QPushButton[variant="go"] {{
    background: {p.green}; border: none; color: {p.on_green}; font-weight: 600;
}}
QPushButton[variant="go"]:hover {{ background: {hover_go}; }}
QPushButton[variant="go"]:disabled {{ background: {tint(p.green, 0.30)}; color: {tint(p.on_green, 0.55)}; }}

/* destructive-action：实心红胶囊 */
QPushButton[variant="danger"] {{
    background: {p.red}; border: none; color: #ffffff; font-weight: 600;
}}
QPushButton[variant="danger"]:hover {{ background: {hover_danger}; }}
QPushButton[variant="danger"]:pressed {{ background: {_shift(p.red, p, 0.20)}; }}
QPushButton[variant="danger"]:disabled {{ background: {tint(p.red, 0.30)}; color: {tint("#ffffff", 0.55)}; }}

QPushButton[variant="flat"] {{
    background: transparent; border: none; color: {p.text_dim};
    padding: 6px 14px; min-height: 26px;
}}
QPushButton[variant="flat"]:hover {{ background: {p.surface_alt}; color: {p.text}; }}
QPushButton[variant="flat"]:checked {{ background: {p.surface_alt}; color: {p.text}; }}
QPushButton[variant="flat"]:disabled {{ background: transparent; color: {p.muted}; }}

QPushButton[variant="step"] {{
    background: {p.surface_alt}; border: none; border-radius: 14px;
    padding: 4px 0; font-size: 14px; font-weight: 600; color: {p.text_dim};
    min-height: 28px;
}}
QPushButton[variant="step"]:hover {{ background: {hover_alt}; color: {p.accent}; }}

/* 分段控件：档位那一排共用边框，只有首尾带圆角 */
QPushButton[variant="seg"] {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 0;
    border-left-width: 0; padding: 6px 0; color: {p.text_dim}; min-height: 28px;
}}
QPushButton[variant="seg"][seg="first"] {{
    border-left-width: 1px; border-top-left-radius: 8px; border-bottom-left-radius: 8px;
}}
QPushButton[variant="seg"][seg="last"] {{
    border-top-right-radius: 8px; border-bottom-right-radius: 8px;
}}
QPushButton[variant="seg"]:hover {{
    background: {hover_alt}; color: {p.text}; border-color: {p.border_strong};
}}
QPushButton[variant="seg"]:pressed {{ background: {press_alt}; }}

QSlider::groove:horizontal {{
    background: {p.bg}; border: none; border-radius: 3px; height: 6px;
}}
QSlider::sub-page:horizontal {{
    background: {p.accent}; border: none; border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {p.knob}; border: 1px solid {p.knob_border};
    width: 15px; height: 15px; margin: -6px 0; border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {p.accent}; border-color: {p.accent}; }}
QSlider::handle:horizontal:disabled {{ background: {p.border_strong}; border-color: {p.border_strong}; }}

/* GNOME 的勾选是圆的；单选 = 粗圈 + 中点 */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 8px;
    border: 1px solid {p.border_strong}; background: transparent;
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
QCheckBox:disabled {{ color: {p.muted}; }}
QCheckBox::indicator:disabled {{ border-color: {p.border}; }}

QRadioButton {{ color: {p.text}; spacing: 6px; }}
QRadioButton::indicator {{
    width: 15px; height: 15px; border-radius: 8px;
    border: 1px solid {p.border_strong}; background: transparent;
}}
QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QRadioButton::indicator:checked {{
    border: 4px solid {p.accent}; background: {p.view};
}}
QRadioButton:disabled {{ color: {p.muted}; }}

QLineEdit {{
    background: {p.view}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {p.accent};
}}
QLineEdit:focus {{ border-color: {p.accent}; }}
QLineEdit:disabled {{ color: {p.muted}; background: {p.surface_alt}; }}

QListWidget {{
    background: {p.view}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 4px; outline: 0;
}}
QListWidget::item {{ padding: 7px 9px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {tint(p.accent, 0.10)}; }}
QListWidget::item:selected {{ background: {tint(p.accent, 0.22)}; color: {p.text}; }}

QComboBox {{
    background: {p.surface_alt}; border: none; border-radius: 16px;
    padding: 6px 12px; min-height: 28px;
}}
QComboBox:hover {{ background: {hover_alt}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p.surface}; border: 1px solid {p.border_strong}; border-radius: 12px;
    selection-background-color: {tint(p.accent, 0.22)}; outline: 0;
}}

/* GNOME 两个明暗主题下 tooltip 都是深底白字 —— 写死，不走调色板 */
QToolTip {{
    background: #202024; color: #f6f6f6;
    border: none; border-radius: 8px; padding: 7px 9px;
}}

QTextEdit {{
    background: {p.view}; border: 1px solid {p.border}; border-radius: 8px; padding: 9px;
    font-family: "Adwaita Mono", "JetBrains Mono", "Noto Sans Mono CJK SC", monospace;
    font-size: 12px; color: {p.text_dim};
}}
QPlainTextEdit {{
    background: {p.view}; border: 1px solid {p.border}; border-radius: 8px; padding: 8px;
    font-family: "Adwaita Mono", "JetBrains Mono", "Noto Sans Mono CJK SC", monospace;
    font-size: 12px; color: {p.text_dim};
    selection-background-color: {tint(p.accent, 0.30)};
}}

QScrollArea {{ background: transparent; border: none; }}
QSplitter::handle {{ background: {p.border}; height: 1px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {p.border_strong}; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QProgressBar {{
    background: {p.bg}; border: 1px solid {p.border}; border-radius: 6px;
    height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 5px; }}

/* ── 无边框卡片外壳（shell.py）+ 自定义标题栏（titlebar.py）───────────
   这三段是"例程"那套界面的样式：卡片外壳、? 菜单、子对话框。
   组件只给 objectName（#appCard / #titleBar / #tbButton / #tbHelp…），
   颜色照样全从这里出。 */
QWidget#root[frameless="true"] {{ background: transparent; }}
/* appCard 是"窗框"不是内容：用 bg 深色当 chrome，内容卡片用 surface 浮在上面
   —— 两层同色的话整个窗口糊成一片，交界只剩 1px 描边（踩过：全窗口一个灰）。
   标题栏区因此自然成为深色 headerbar，浅色下是灰壳白卡（nemo 的观感）。 */
QFrame#appCard {{
    background: {p.bg};
    border: 1px solid {p.border};
    border-radius: 12px;
}}
QFrame#appCard[cardState="maximized"] {{ border-radius: 0; border: none; }}
QWidget#titleBar {{ background: transparent; }}
QLabel#appTitle {{ font-size: 13px; font-weight: 600; color: {p.text}; padding-left: 2px; }}
QToolButton#tbButton, QToolButton#tbHelp {{
    background: transparent; border: none; border-radius: 11px;
    color: {p.muted}; font-size: 13px;
}}
QToolButton#tbButton:hover, QToolButton#tbHelp:hover {{
    background: {p.surface_alt}; color: {p.text};
}}
QToolButton#tbButton:pressed, QToolButton#tbHelp:pressed {{ background: {p.border_strong}; }}
QToolButton#tbHelp {{ border: 1px solid {p.border}; border-radius: 11px; }}
/* ? 是弹出菜单的按钮：不要那个下拉小箭头 */
QToolButton#tbButton::menu-indicator, QToolButton#tbHelp::menu-indicator {{ image: none; width: 0; }}
QToolButton#tbButton[kind="close"]:hover {{ background: {p.red}; color: #ffffff; }}
QSizeGrip {{ background: transparent; }}

/* ── 菜单（? 里那个）：popover —— 12px 圆角 + 同色分隔 ───────── */
QMenu {{
    background: {p.surface}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 12px; padding: 6px;
}}
QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: 8px; }}
QMenu::item:selected {{ background: {p.surface_alt}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}

/* ── 子对话框（shell.DialogShell）───────────────────────── */
QDialog {{ background: {p.surface}; }}
QDialog QLabel {{ color: {p.text}; }}
QTextBrowser {{
    background: {p.view}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 8px; padding: 6px;
}}
QTextBrowser h3 {{ color: {p.text}; }}
QTextBrowser code {{ color: {p.accent}; }}
QPushButton#primary {{
    background: {p.accent}; color: {p.on_accent};
    border: none; font-weight: 600;
}}

/* ── 标签页 ───────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {p.border}; border-radius: 8px;
    background: {p.surface}; top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {p.muted};
    padding: 6px 14px; margin-right: 4px;
    border: 1px solid transparent; border-radius: 8px;
}}
QTabBar::tab:selected {{
    background: {p.surface_alt}; color: {p.text}; border-color: {p.border};
}}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}

/* ── 补齐几个还没覆盖的控件族（设置面板/对话框里会用到）──── */
/* GroupBox 照 GNOME 的 preferences group 画：就是一张卡片 */
QGroupBox {{
    background: {p.surface};
    border: 1px solid {p.border}; border-radius: 12px;
    margin-top: 14px; padding: 12px 10px 8px 10px; color: {p.text};
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p.muted};
}}
QSpinBox, QDoubleSpinBox {{
    background: {p.view}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 8px; padding: 5px 8px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {p.accent}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {p.muted}; background: {p.surface_alt}; }}
QTableWidget, QTreeWidget {{
    background: {p.view}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 8px;
    alternate-background-color: {p.surface_alt};
}}
QTableWidget::item:selected, QTreeWidget::item:selected {{
    background: {p.accent}; color: {p.on_accent};
}}
QHeaderView::section {{
    background: {p.surface_alt}; color: {p.muted};
    border: none; border-bottom: 1px solid {p.border}; padding: 5px 8px;
}}
"""


def repolish(widget: QWidget) -> None:
    """属性选择器（[tone=...]）改了属性后必须 repolish 才会重画。"""
    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _build_palette(p: Palette) -> QPalette:
    """把 token 装进 Qt 调色板 —— 没被 QSS 覆盖的原生控件（QMessageBox 的细节、
    输入法候选等）也跟配色走，Qt 调色板探测（_qt_scheme）从此也能答对。"""
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(p.bg))
    palette.setColor(QPalette.WindowText, QColor(p.text))
    palette.setColor(QPalette.Base, QColor(p.view))
    palette.setColor(QPalette.AlternateBase, QColor(p.surface_alt))
    palette.setColor(QPalette.Text, QColor(p.text))
    palette.setColor(QPalette.Button, QColor(p.surface_alt))
    palette.setColor(QPalette.ButtonText, QColor(p.text))
    palette.setColor(QPalette.Highlight, QColor(p.accent))
    palette.setColor(QPalette.HighlightedText, QColor(p.on_accent))
    palette.setColor(QPalette.ToolTipBase, QColor("#202024"))
    palette.setColor(QPalette.ToolTipText, QColor("#f6f6f6"))
    palette.setColor(QPalette.Link, QColor(p.accent))
    palette.setColor(QPalette.PlaceholderText, QColor(p.muted))
    return palette


def apply(app: QApplication, force: Optional[str] = None) -> str:
    """算出该用哪套配色并装上（样式表 + 调色板）。返回实际生效的 "dark" / "light"。"""
    global _current
    scheme, palette = resolve(force)
    _current = palette
    app.setPalette(_build_palette(palette))
    app.setStyleSheet(build_style(palette))
    for widget in app.allWidgets():
        repolish(widget)
    return scheme


class ThemeWatcher(QObject):
    """盯着桌面明暗，变了就发 changed。

    优先走 XDG 门户的 SettingChanged 信号（桌面切明暗即时到达，无需轮询）；
    订阅不上就退回固定间隔问一次 gsettings（一次调用十几毫秒）。两条路都接上：
    门户没起 / 没实现时轮询兜底，Qt 的 paletteChanged 平台主题生效时更即时。
    """

    changed = pyqtSignal(str)          # "dark" / "light"

    def __init__(self, interval_ms: int = 4000, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._scheme = detect_scheme()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)
        self._portal_ok = self._subscribe_portal()
        app = QApplication.instance()
        if app is not None:
            app.paletteChanged.connect(self._poll)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # ── 门户信号（即时）────────────────────────────────────
    def _subscribe_portal(self) -> bool:
        try:
            from PyQt5.QtDBus import QDBusConnection
        except ImportError:
            return False
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        return bus.connect(
            PORTAL_SERVICE, PORTAL_PATH, PORTAL_SETTINGS, "SettingChanged",
            self._on_portal_setting,
        )

    # ⚠️ 槽签名必须是 (str, str, QDBusVariant) 且**类级方法 + @pyqtSlot**：
    # 实测 (str, str, int) 能 connect 但信号会被静默丢掉（'v' 变体转不成 int），
    # 不加 @pyqtSlot 则 connect 直接拒绝。
    def _on_portal_setting(self, namespace: str, key: str,
                           value: object) -> None:
        if namespace != PORTAL_NAMESPACE or key != PORTAL_KEY:
            return
        raw = value
        if hasattr(raw, "variant"):        # QDBusVariant → 里面的 QVariant
            raw = raw.variant()
        if hasattr(raw, "value"):          # QVariant → Python 值
            raw = raw.value()
        try:
            code = int(raw)                # 0=无偏好 1=深 2=浅
        except (TypeError, ValueError):
            return
        if code == 1:
            self._emit("dark")
        elif code == 2:
            self._emit("light")
        else:
            self._poll()                   # 桌面没设偏好：按整条探测链路重算

    if QDBusVariant is not None:           # 补上槽签名（类型串要求见模块顶部注释）
        _on_portal_setting = pyqtSlot(str, str, "QDBusVariant")(_on_portal_setting)

    # ── 轮询兜底 ───────────────────────────────────────────
    def _poll(self, *_args: object) -> None:
        self._emit(detect_scheme())

    def _emit(self, scheme: str) -> None:
        if forced_scheme() is not None:   # 被钉死了就不跟随
            return
        if scheme != self._scheme and scheme in ("dark", "light"):
            self._scheme = scheme
            self.changed.emit(scheme)
