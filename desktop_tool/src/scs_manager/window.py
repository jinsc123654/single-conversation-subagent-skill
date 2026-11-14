"""Optional project/state browser; all filesystem/database jobs are asynchronous."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import signal
import sys
import time

from PyQt5.QtCore import QDir, QProcess, QProcessEnvironment, QTimer, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFileSystemModel, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QTreeView, QVBoxLayout, QWidget)

from . import __version__, theme
from .backend import Registry, canonical, docs_root, inside
from .sidebar import ProjectTree

STATE_LABELS = {"PENDING": "待开始", "IN_PROGRESS": "进行中", "REVIEW_READY": "待主对话验收", "BLOCKED": "受阻",
                "COMPLETED": "已完成", "ABANDONED": "已放弃"}
ATTEMPT_LABELS = {"PREPARED": "已准备", "LAUNCHING": "启动中", "RUNNING": "运行中",
                  "DRAINING": "收尾中", "ENDED": "已结束", "UNKNOWN": "失联"}
CLOSED_STATES = {"COMPLETED", "ABANDONED"}


def attempt_summary(package):
    active = sum(1 for item in package.get("attempts", []) if item["state"] not in {"ENDED", "UNKNOWN"})
    unknown = sum(1 for item in package.get("attempts", []) if item["state"] == "UNKNOWN")
    return f"运行 {active} · 未知 {unknown}" if active or unknown else "—"


def age_days(stamp):
    if not stamp:
        return None
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - value).days)


def package_state_label(package):
    label = STATE_LABELS.get(package["state"], package["state"])
    if package["state"] in CLOSED_STATES:
        return label
    days = age_days(package.get("updated_at") or package.get("created_at"))
    return label + (f" · 滞留 {days} 天" if days is not None and days >= 7 else "")


class ClosePackageDialog(QDialog):
    def __init__(self, package, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关闭工作包")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        title = QLabel(package["id"] + "\n" + package["goal"])
        title.setWordWrap(True)
        layout.addWidget(title)
        self.completed = QRadioButton("已完成：主对话验收通过")
        self.abandoned = QRadioButton("已放弃：被取代、重复或不再需要")
        self.abandoned.setChecked(True)
        if package.get("state") != "REVIEW_READY":
            self.completed.setEnabled(False)
            self.completed.setToolTip("只有“待主对话验收”的工作包才能标记完成")
        layout.addWidget(self.completed)
        layout.addWidget(self.abandoned)
        layout.addWidget(QLabel("关闭原因（必填，写入审计记录）"))
        self.reason = QTextEdit()
        self.reason.setPlaceholderText("例如：由 WP-108/R1 结案报告取代")
        self.reason.setFixedHeight(90)
        layout.addWidget(self.reason)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        if not self.reason.toPlainText().strip():
            QMessageBox.warning(self, "需要原因", "关闭工作包必须写明原因。")
            return
        self.accept()

    def values(self):
        return ("COMPLETED" if self.completed.isChecked() else "ABANDONED",
                self.reason.toPlainText().strip())


class MainWindow(QMainWindow):
    def __init__(self, registry_path, *, initial_projects=(), auto_load=True):
        super().__init__()
        self.setWindowTitle("SCS 项目管理器")
        self.resize(1220, 820)
        self.setMinimumSize(760, 560)
        self.registry = Registry(registry_path)
        self.settings = self.registry.load()
        for root in initial_projects:
            value = str(canonical(root))
            if value not in self.settings["projects"]:
                self.settings["projects"].append(value)
            if value in self.settings.get("hidden_projects", []):
                self.settings["hidden_projects"].remove(value)
        self.catalog = {}
        self.current_root = None
        self.current_snapshot = None
        self.visible_packages = []
        self.busy = False
        self.closing = False
        self.last_error = ""
        self.job = QProcess(self)
        self.job.readyReadStandardOutput.connect(self.read_output)
        self.job.readyReadStandardError.connect(self.drain_error)
        self.job.finished.connect(self.finished)
        self.job.errorOccurred.connect(self.failed_start)
        self.catalog_job = QProcess(self)
        self.catalog_job.readyReadStandardOutput.connect(self.read_catalog_output)
        self.catalog_job.readyReadStandardError.connect(lambda: self.catalog_job.readAllStandardError())
        self.catalog_job.finished.connect(self.catalog_finished)
        self.catalog_job.errorOccurred.connect(self.catalog_failed)
        self.catalog_timeout = QTimer(self)
        self.catalog_timeout.setSingleShot(True)
        self.catalog_timeout.timeout.connect(lambda: self.kill_owned(self.catalog_job))
        self.catalog_output = bytearray()
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.cancel_job)
        self.poll = QTimer(self)
        self.poll.setInterval(8000)
        self.poll.timeout.connect(self.poll_snapshot)
        self.poll.start()
        self._build()
        self.rebuild_projects()
        if auto_load and self.projects.count():
            self.projects.setCurrentRow(0)
        if auto_load:
            QTimer.singleShot(0, self.refresh_catalog)
        self.theme_watcher = theme.ThemeWatcher(parent=self)
        self.theme_watcher.changed.connect(lambda _s: theme.apply(QApplication.instance()))
        self.theme_watcher.start()

    def _build(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        top = QHBoxLayout()
        title = QLabel("项目与 MCP 状态")
        title.setObjectName("appTitle")
        top.addWidget(title)
        top.addStretch()
        self.theme_box = QComboBox()
        self.theme_box.addItems(["跟随桌面", "浅色", "深色"])
        self.theme_box.currentIndexChanged.connect(self.switch_theme)
        top.addWidget(self.theme_box)
        self.button(top, "帮助 / 关于", self.help)
        layout.addLayout(top)
        split = QSplitter()
        side = QWidget()
        sidebox = QVBoxLayout(side)
        sidebox.setContentsMargins(0, 0, 8, 0)
        sidebox.addWidget(QLabel("项目 · 最近活动优先"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("筛选名称或路径")
        self.search.textChanged.connect(self.filter_projects)
        sidebox.addWidget(self.search)
        self.projects = ProjectTree()
        self.projects.itemCollapsed.connect(lambda item: self.save_collapse(item,True))
        self.projects.itemExpanded.connect(lambda item: self.save_collapse(item,False))
        self.projects.currentItemChanged.connect(self.select_project)
        sidebox.addWidget(self.projects, 1)
        row = QHBoxLayout()
        self.button(row, "添加项目", self.add_project)
        self.button(row, "发现项目", self.scan_projects)
        sidebox.addLayout(row)
        row = QHBoxLayout()
        self.button(row, "移出列表", self.remove_project)
        self.button(row, "设置 MCP Python", self.select_python)
        sidebox.addLayout(row)
        note = QLabel("只扫描你选择的目录及下两层。\n添加项目不会创建状态库。")
        note.setProperty("tone", "muted")
        note.setWordWrap(True)
        sidebox.addWidget(note)
        split.addWidget(side)
        main = QWidget()
        body = QVBoxLayout(main)
        body.setContentsMargins(8, 0, 0, 0)
        self.project_name = QLabel("选择一个项目")
        self.project_name.setObjectName("title")
        self.project_path = QLabel("添加项目，或在指定目录中发现已有 MCP 项目。")
        self.project_path.setTextFormat(Qt.PlainText)
        self.project_path.setWordWrap(True)
        self.project_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.project_name)
        body.addWidget(self.project_path)
        self.summary = QLabel("本地数据：—　工作包：—　受管视图：—")
        self.summary.setWordWrap(True)
        body.addWidget(self.summary)
        self.connection = QLabel("宿主连接：不可观测　·　尚未执行连接自检")
        self.connection.setProperty("tone", "muted")
        self.connection.setWordWrap(True)
        body.addWidget(self.connection)
        actions = QHBoxLayout()
        self.project_actions = []
        for label, fn in (("打开项目", self.open_project), ("打开文档库", self.open_docs),
                          ("用 Obsidian 打开", self.open_obsidian), ("刷新状态", lambda: self.start_job("snapshot")),
                          ("连接自检", lambda: self.start_job("probe"))):
            self.project_actions.append(self.button(actions, label, fn))
        body.addLayout(actions)
        self.tabs = QTabWidget()
        folders = QWidget()
        fb = QVBoxLayout(folders)
        row = QHBoxLayout()
        self.button(row, "打开选中目录", self.open_selection)
        self.button(row, "选中项 → Obsidian", lambda: self.open_obsidian(selected=True))
        self.button(row, "复制路径", self.copy_selection)
        fb.addLayout(row)
        self.files = QFileSystemModel(self)
        self.files.setReadOnly(True)
        self.files.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden)
        self.tree = QTreeView()
        self.tree.setModel(self.files)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setDragEnabled(False)
        self.tree.setEnabled(False)
        self.tree.setColumnWidth(0, 330)
        self.tree.doubleClicked.connect(lambda _index: self.open_selection())
        fb.addWidget(self.tree)
        self.tabs.addTab(folders, "项目目录")
        self.packages = QTableWidget(0, 5)
        self.packages.setHorizontalHeaderLabels(["工作包 / 目标", "修订", "状态", "子任务数", "执行"])
        self.packages.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.packages.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.packages.horizontalHeader().setStretchLastSection(True)
        self.packages.setColumnWidth(0, 380)
        self.packages.itemSelectionChanged.connect(self.update_close_button)
        package_tab = QWidget()
        package_layout = QVBoxLayout(package_tab)
        package_toolbar = QHBoxLayout()
        self.show_closed = QCheckBox("显示已关闭")
        self.show_closed.setChecked(bool(self.settings.get("show_closed", False)))
        self.show_closed.toggled.connect(self.toggle_closed)
        package_toolbar.addWidget(self.show_closed)
        self.close_button = self.button(package_toolbar, "关闭工作包…", self.close_package)
        self.close_button.setEnabled(False)
        package_toolbar.addStretch()
        package_layout.addLayout(package_toolbar)
        package_layout.addWidget(self.packages, 1)
        self.tabs.addTab(package_tab, "工作包")
        self.attempts = QTableWidget(0, 7)
        self.attempts.setHorizontalHeaderLabels(["工作包 / 子任务", "attempt", "状态", "宿主", "结果", "最后观测", "资源"])
        self.attempts.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.attempts.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.attempts.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.attempts, "执行实例")
        view = QWidget()
        vb = QVBoxLayout(view)
        view.setMinimumHeight(550)
        self.view_info = QLabel("选择项目后查看受管文件。")
        self.view_info.setWordWrap(True)
        self.view_info.setTextFormat(Qt.PlainText)
        vb.addWidget(self.view_info)
        self.sessions = QComboBox()
        self.sessions.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        vb.addWidget(QLabel("刷新使用的已确认会话（不会替你回答工作模式或改任务结果）"))
        vb.addWidget(self.sessions)
        row = QHBoxLayout()
        self.button(row, "打开受管视图", self.open_view)
        self.button(row, "刷新 / 初始化视图…", self.refresh_view)
        self.button(row, "归档整个视图…", self.archive_view)
        vb.addLayout(row)
        note = QLabel("归档保留批注及额外笔记，暂停自动刷新；不会删除数据库、记忆、证据或 .obsidian。\n"
                      "恢复：关闭编辑器并暂停 MCP 写入后，将归档目录移回 acceptance（目标须不存在），再连接检查。")
        note.setWordWrap(True)
        note.setProperty("tone", "muted")
        vb.addWidget(note)
        vb.addWidget(QLabel("MCP 受管文件（双击打开包含目录）"))
        self.managed_files = QListWidget()
        self.managed_files.itemDoubleClicked.connect(lambda item: self.safe(lambda: self.open_path(item.text())))
        vb.addWidget(self.managed_files, 1)
        self.view_details = QTextBrowser()
        self.view_details.setOpenExternalLinks(False)
        vb.addWidget(self.view_details, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(view)
        view.setAutoFillBackground(False)
        view.setObjectName("scrollBody")
        self.tabs.addTab(scroll, "生成文件管理")
        self.events = QTextBrowser()
        self.events.setOpenExternalLinks(False)
        self.tabs.addTab(self.events, "最近记录")
        body.addWidget(self.tabs, 1)
        split.addWidget(main)
        split.setSizes([290, 900])
        layout.addWidget(split, 1)
        self.status = QLabel("就绪 · 可选工具，关闭窗口不会停止宿主中的 MCP")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setCentralWidget(container)

    @staticmethod
    def button(row, label, callback):
        button = QPushButton(label)
        button.clicked.connect(lambda _checked=False: callback())
        row.addWidget(button)
        return button

    def switch_theme(self, index):
        theme.set_forced((None, "light", "dark")[index])
        theme.apply(QApplication.instance())

    def persist(self):
        self.registry.save(self.settings)

    def toggle_closed(self, checked):
        self.settings["show_closed"] = bool(checked)
        self.safe(self.persist)
        self.render_packages()

    def render_packages(self):
        packages = (self.current_snapshot or {}).get("packages", [])
        show_closed = self.show_closed.isChecked()
        self.visible_packages = [p for p in packages if show_closed or p["state"] not in CLOSED_STATES]
        self.packages.setRowCount(len(self.visible_packages))
        for row, package in enumerate(self.visible_packages):
            values = (package["id"] + "\n" + package["goal"], package["revision"],
                      package_state_label(package), package["tasks"], attempt_summary(package))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(package.get("close_reason") or str(value))
                self.packages.setItem(row, column, item)
        self.packages.resizeRowsToContents()
        self.update_close_button()

    def render_attempts(self):
        rows = []
        for package in (self.current_snapshot or {}).get("packages", []):
            for attempt in package.get("attempts", []):
                rows.append((package, attempt))
        self.attempts.setRowCount(len(rows))
        for row, (package, attempt) in enumerate(rows):
            resources = ", ".join(item.get("resource", "") for item in attempt.get("resources", [])) or "—"
            values = (package["id"] + " / " + attempt["subtask_id"], attempt["id"],
                      ATTEMPT_LABELS.get(attempt["state"], attempt["state"]),
                      attempt.get("host_agent_id") or "—", attempt.get("result_state") or "—",
                      attempt.get("last_observed_at") or attempt.get("updated_at") or "—", resources)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.attempts.setItem(row, column, item)
        self.attempts.resizeRowsToContents()

    def selected_package(self):
        row = self.packages.currentRow()
        return self.visible_packages[row] if 0 <= row < len(self.visible_packages) else None

    def update_close_button(self):
        package = self.selected_package()
        self.close_button.setEnabled(bool(package) and package["state"] not in CLOSED_STATES
                                      and package.get("active_attempts", 0) == 0)

    def close_package(self):
        package = self.selected_package()
        if not package or self.busy:
            return
        dialog = ClosePackageDialog(package, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        state, reason = dialog.values()
        self.start_job("close", payload={"package": package["id"], "state": state,
            "reason": reason, "version": package["version"]})

    def rebuild_projects(self):
        hidden = set(self.settings.get("hidden_projects", []))
        roots = dict.fromkeys([*self.settings["projects"], *self.catalog])
        entries = [self.catalog.get(root, {"root": root, "name": Path(root).name, "last_activity": 0, "packages": []})
                   for root in roots if root not in hidden]
        self.projects.update_projects(entries,self.settings.get("collapsed_projects",{}),self.settings.get("last_opened",{}))
        self.filter_projects()

    def filter_projects(self):
        self.projects.filter_projects(self.search.text())

    def save_collapse(self,item,collapsed):
        if item.parent() is not None:
            return
        self.settings.setdefault("collapsed_projects",{})[item.data(0,Qt.UserRole)]=collapsed
        self.safe(self.persist)

    def record_open(self):
        self.settings.setdefault("last_opened",{})[self.current_root]=time.time()
        self.persist()
        self.rebuild_projects()

    def refresh_catalog(self):
        if self.closing or self.catalog_job.state() != QProcess.NotRunning:
            return
        self.catalog_output=bytearray()
        env=QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH",str(Path(__file__).resolve().parents[1]))
        self.catalog_job.setProcessEnvironment(env)
        self.catalog_job.start(sys.executable, ["-m", "scs_manager.worker", "catalog", "--root", "."])
        request = {"projects": self.settings["projects"], "hidden_projects": self.settings.get("hidden_projects", [])}
        self.catalog_job.write(json.dumps(request, ensure_ascii=False).encode())
        self.catalog_job.closeWriteChannel()
        self.catalog_timeout.start(20000)

    def read_catalog_output(self):
        self.catalog_output.extend(bytes(self.catalog_job.readAllStandardOutput()))
        if len(self.catalog_output)>4_000_000:
            self.kill_owned(self.catalog_job)

    def catalog_failed(self,error):
        if error==QProcess.FailedToStart:
            self.catalog_timeout.stop()
            self.status.setText("项目排序信息暂不可读取")
            if self.closing and not self.busy:
                QTimer.singleShot(0,self.close)

    def catalog_finished(self,code,_status):
        self.catalog_timeout.stop()
        self.read_catalog_output()
        if not code and len(self.catalog_output)<=4_000_000:
            try:
                reply=json.loads(self.catalog_output)
                if reply.get("ok"):
                    self.catalog={entry["root"]:entry for entry in reply["data"]}
                    self.rebuild_projects()
            except (ValueError,KeyError,TypeError):
                self.status.setText("项目排序信息暂不可读取")
        if self.closing and not self.busy:
            QTimer.singleShot(0,self.close)

    def add_paths(self, paths):
        for path in paths:
            root = str(canonical(path))
            if root in self.settings.get("hidden_projects", []):
                self.settings["hidden_projects"].remove(root)
            if root not in self.settings["projects"]:
                self.settings["projects"].append(root)
        self.persist()
        self.rebuild_projects()
        if self.projects.count():
            for i in range(self.projects.count()):
                if self.projects.item(i).data(0,Qt.UserRole)==str(canonical(paths[-1])):
                    self.projects.setCurrentRow(i)
                    break
        self.refresh_catalog()

    def add_project(self):
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(self, "选择项目根目录")
        if path:
            self.safe(lambda: self.add_paths([path]))

    def scan_projects(self):
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(self, "只扫描该目录及下两层")
        if path:
            self.start_job("scan", path)

    def remove_project(self):
        item = self.projects.currentItem()
        if item and not self.busy:
            root = item.data(0, Qt.UserRole)
            if root in self.settings["projects"]:
                self.settings["projects"].remove(root)
            hidden = self.settings.setdefault("hidden_projects", [])
            hidden.append(root)
            del hidden[:-500]
            self.safe(self.persist)
            self.current_root = None
            self.current_snapshot = None
            self.rebuild_projects()
            self.project_name.setText("选择一个项目")
            self.project_path.setText("已移出列表；项目文件保留。")
            self.tree.setRootIndex(self.files.index(""))
            self.tree.setEnabled(False)
            self.packages.setRowCount(0)
            self.attempts.setRowCount(0)
            self.sessions.clear()
            self.summary.setText("本地数据：—　工作包：—　受管视图：—")
            self.view_info.setText("尚未选择项目")
            self.view_details.clear()
            self.managed_files.clear()
            self.events.clear()
            self.connection.setText("宿主连接：不可观测　·　尚未执行连接自检")
            if self.projects.count():
                self.projects.setCurrentRow(0)

    def select_python(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择装有 scs-mcp / mcp 2.3 的 Python 解释器")
        if path:
            self.settings["server_python"] = path
            self.safe(self.persist)

    def select_project(self, item, _old=None):
        if item is None or self.busy:
            return
        root=item.data(0,Qt.UserRole)
        package=item.data(0,Qt.UserRole+1)
        if root==self.current_root and self.current_snapshot:
            if package:
                self.select_package(package)
            return
        self.current_root=root
        self.current_snapshot=None
        self.project_name.setTextFormat(Qt.PlainText)
        self.project_name.setText(Path(root).name)
        self.project_path.setText(root)
        self.connection.setText("宿主连接：不可观测 · 尚未执行连接自检")
        self.packages.setRowCount(0)
        self.attempts.setRowCount(0)
        self.sessions.clear()
        self.events.clear()
        self.view_details.clear()
        self.managed_files.clear()
        self.view_info.setText("读取中…")
        self.summary.setText("读取本地数据…")
        self.tree.setEnabled(True)
        self.files.setRootPath(root)
        self.tree.setRootIndex(self.files.index(root))
        self.start_job("snapshot")

    def select_package(self, package):
        for row, item in enumerate(self.visible_packages):
            if item["id"] == package:
                self.packages.selectRow(row)
                self.tabs.setCurrentIndex(1)
                return
        for item in (self.current_snapshot or {}).get("packages", []):
            if item["id"] == package and item["state"] in CLOSED_STATES and not self.show_closed.isChecked():
                self.show_closed.setChecked(True)
                for row, visible in enumerate(self.visible_packages):
                    if visible["id"] == package:
                        self.packages.selectRow(row)
                        self.tabs.setCurrentIndex(1)
                        return

    def selected_path(self):
        index = self.tree.currentIndex()
        return self.files.filePath(index) if index.isValid() else self.current_root

    def safe(self, fn):
        try:
            return fn()
        except Exception as error:
            self.last_error = str(error)
            self.status.setText("操作未完成：" + str(error))
            QMessageBox.warning(self, "操作未完成", str(error))

    def open_path(self, path):
        if not self.current_root:
            return
        path = inside(self.current_root, path)
        if not path.exists():
            raise ValueError("目录尚未生成：" + str(path))
        if not path.is_dir():
            path = path.parent  # Never execute/open arbitrary files from the browser.
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            raise ValueError("系统未能打开目录，请复制路径后手动打开")
        self.record_open()

    def open_project(self):
        if self.current_root:
            self.safe(lambda: self.open_path(self.current_root))

    def open_docs(self):
        if self.current_root:
            self.safe(lambda: self.open_path(docs_root(self.current_root)))

    def open_selection(self):
        if self.current_root:
            self.safe(lambda: self.open_path(self.selected_path()))

    def copy_selection(self):
        if self.current_root:
            self.safe(lambda: QApplication.clipboard().setText(str(inside(self.current_root, self.selected_path()))))

    def open_view(self):
        if self.current_root:
            self.safe(lambda: self.open_path(docs_root(self.current_root) / "agent/acceptance"))

    def open_obsidian(self, selected=False):
        if not self.current_root or self.busy:
            return
        self.start_job("prepare-obsidian",selection=self.selected_path() if selected else "")

    def refresh_view(self):
        if not self.current_root or self.busy:
            return
        if not self.sessions.currentData():
            QMessageBox.information(self, "需要已确认会话", "先在主对话完成模式与验证选择，再刷新状态并选择该会话。")
            return
        path = docs_root(self.current_root) / "agent/acceptance"
        if QMessageBox.question(self, "刷新受管视图", "将从 MCP 数据更新：\n" + str(path) +
            "\n\n保留 manual 标记内批注；生成区冲突会停止。缺少库配置时创建空 .obsidian/app.json。\n"
            "启用后续工作包变更的自动刷新。继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.start_job("refresh")

    def archive_view(self):
        if not self.current_root or self.busy:
            return
        path = docs_root(self.current_root) / "agent/acceptance"
        if QMessageBox.question(self, "归档整个视图", "将移动整个目录（包括批注及额外笔记）：\n" + str(path) +
            "\n→ 同级 view-archives/时间编号\n\n请先关闭正在编辑这些文件的窗口。归档后自动刷新暂停；数据库、证据及库配置保留。继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.start_job("archive")

    def poll_snapshot(self):
        if self.isVisible() and not self.isMinimized():
            self.refresh_catalog()
            if self.current_root and not self.busy:
                self.start_job("snapshot", quiet=True)

    def start_job(self, action, root=None, quiet=False, selection="", payload=None):
        root = root or self.current_root
        if self.busy or not root:
            return
        self.busy = True
        self.action = action
        self.quiet = quiet
        self.output = bytearray()
        self.job_error = ""
        self.projects.setEnabled(False)
        self.status.setText("正在" + {"snapshot": "读取状态", "probe": "执行短期 stdio 自检", "refresh": "刷新视图",
            "scan": "发现项目", "archive": "归档视图", "prepare-obsidian": "准备 Obsidian 文档库",
            "close": "关闭工作包"}[action] + "…") if not quiet else None
        base = Path(__file__).resolve().parents[3]
        default_python = base / ".venv/bin/python"
        server_python = self.settings.get("server_python") or (str(default_python) if default_python.exists() else sys.executable)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
        self.job.setProcessEnvironment(env)
        self.job.start(sys.executable, ["-m", "scs_manager.worker", action, "--root", str(root),
            "--python", server_python, "--session", self.sessions.currentData() or "", "--selection", selection])
        if payload is not None:
            self.job.write(json.dumps(payload, ensure_ascii=False).encode())
            self.job.closeWriteChannel()
        self.timeout.start(28000)

    def read_output(self):
        self.output.extend(bytes(self.job.readAllStandardOutput()))
        if len(self.output) > 2_000_000:
            self.job_error = "响应超过限制"
            self.cancel_job()

    def drain_error(self):
        self.job.readAllStandardError()  # Drain without persisting arbitrary SDK stderr.

    @staticmethod
    def kill_owned(job):
        pid=int(job.processId())
        try:
            if pid and os.name=="posix" and os.getpgid(pid)==pid:
                os.killpg(pid,signal.SIGKILL)
            elif job.state()!=QProcess.NotRunning:
                job.kill()
        except ProcessLookupError:
            pass

    def cancel_job(self):
        self.job_error=self.job_error or "操作超时，已回收本窗口的检查进程；请重新读取状态。"
        self.kill_owned(self.job)

    def failed_start(self, error):
        if error == QProcess.FailedToStart:
            self.job_error = "无法启动本工具的工作进程"
            self.finished(-1, QProcess.CrashExit)

    def finished(self, code, _exit_status):
        if not self.busy:
            return
        self.timeout.stop()
        self.read_output()
        self.busy = False
        self.projects.setEnabled(True)
        try:
            if self.job_error:
                raise ValueError(self.job_error)
            reply = json.loads(self.output)
            if not reply.get("ok") or code:
                raise ValueError(reply.get("error", "操作失败"))
            data = reply["data"]
            self.last_error = ""
            if self.action == "prepare-obsidian":
                launch_result=QProcess.startDetached(data["launch_program"],data["launch_arguments"])
                started=launch_result[0] if isinstance(launch_result,tuple) else launch_result
                if not started:
                    raise ValueError("Obsidian 启动失败；请检查安装路径")
                self.record_open()
                self.status.setText("已启动 Obsidian，对应库："+data["vault_root"]+" · 首次配置已自动准备")
                return
            elif self.action == "snapshot":
                self.apply_snapshot(data)
            elif self.action == "scan":
                self.add_paths(data["projects"])
                self.status.setText(f"发现 {len(data['projects'])} 个项目" + ("；已达到扫描上限" if data['truncated'] else ""))
                return
            elif self.action == "probe":
                d = data["diagnostics"]
                self.connection.setText(f"自检成功 · {len(data['tools'])} 个工具 · 服务 {d['server_version']} · {data['checked_at']}\n"
                    "检查实例已结束；宿主连接仍不可观测。")
                self.view_details.setPlainText(json.dumps(data.get("view", {"提示": "选择会话后可检查视图有效性"}), ensure_ascii=False, indent=2))
            else:
                self.view_details.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
                self.status.setText("操作完成：" + data.get("message", data.get("result", "OK")))
                if not self.closing:
                    QTimer.singleShot(0, lambda: self.start_job("snapshot"))
                return
            if not self.quiet:
                self.status.setText("读取完成" if self.action == "snapshot" else "连接自检完成")
        except Exception as error:
            self.last_error = str(error)
            if self.action == "probe":
                self.connection.setText("最近连接自检失败；宿主连接仍不可观测。")
            self.status.setText("操作未完成：" + str(error))
        finally:
            if self.closing:
                QTimer.singleShot(0, self.close)

    def apply_snapshot(self, data):
        if data == self.current_snapshot:
            return
        self.current_snapshot = data
        counts = data.get("counts", {})
        v = data["view"]
        closed = sum(1 for package in data["packages"] if package["state"] in CLOSED_STATES)
        attempts = counts.get("attempts")
        attempt_text = f"　·　执行实例：{attempts}" if attempts is not None else ""
        self.summary.setText(f"本地数据：{data['database']}　·　会话：{counts.get('sessions', 0)}　·　工作包：{counts.get('work_packages', 0)}（已关闭 {closed}）{attempt_text}\n"
                             f"受管视图：{v['state']}　·　{v['count']} 个受管文件")
        selected = self.sessions.currentData()
        self.sessions.clear()
        for s in data["sessions"]:
            if s["mode"] and s["mode"] != "investigate" and (s["mode"] not in {"standard", "long"} or
                s["verification"] and (s["review_registration"] != "registered" or s["codex_review"] is not None)):
                self.sessions.addItem(f"{s['client']} · {s['mode']} · {s['id']}", s["id"])
        index = self.sessions.findData(selected)
        if index >= 0:
            self.sessions.setCurrentIndex(index)
        self.render_packages()
        self.render_attempts()
        self.view_info.setText("库目录：" + data['docs_root'] + "\n受管目录：agent/acceptance\n自动刷新：" +
                               ("启用" if v['auto_refresh'] else "未启用") + "　· 最近生成：" + (v.get('refreshed_at') or "—"))
        self.managed_files.clear()
        self.managed_files.addItems(v.get("files", []))
        item=self.projects.currentItem()
        if item and item.data(0,Qt.UserRole+1):
            self.select_package(item.data(0,Qt.UserRole+1))
        self.events.setPlainText("\n".join(f"{e['at']}  {e['tool']}  {e['event']}  {e['error_code'] or ''}" for e in data['events']) or "暂无 MCP 事件。")

    def help(self):
        QMessageBox.information(self, "帮助 / 关于", f"SCS 项目管理器 {__version__}\n\n"
            "可选 PyQt5 工具：添加/发现项目、浏览目录、查看工作包与 MCP 记录、打开 Obsidian、刷新或归档受管视图。\n"
            "连接自检按需启动短期实例，不管理宿主进程、不代表宿主在线。每 8 秒只读刷新，可见时运行。\n"
            "项目栏可展开/收起，按最近活动排序；折叠状态保存在本工具配置中。移出列表不删除项目。\n"
            "Obsidian 使用项目内独立启动配置，自动准备库，无需手工登记；不改全局库列表。\n\n"
            "本工具无需系统安装。移除工具：关闭窗口后删除工具文件；若需清除列表，删除：\n" + str(self.registry.path) +
            "\n不会安装菜单条目或图标，不修改共享主题。")

    def closeEvent(self, event):
        self.closing=True
        self.poll.stop()
        self.theme_watcher.stop()
        if self.busy or self.catalog_job.state()!=QProcess.NotRunning:
            self.closing = True
            if self.busy:
                self.cancel_job()
            if self.catalog_job.state()!=QProcess.NotRunning:
                self.kill_owned(self.catalog_job)
            event.ignore()
            return
        event.accept()
