"""Local Markdown board: backend snapshot and ranked task page."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp_server/src"))

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QFormLayout

from scs_manager import theme
from scs_manager.backend import board_snapshot
from scs_manager.window import DashboardPage, MainWindow, ProjectCard, TaskDialog, TodoCard, TodoPage
from scs_mcp.board import Board
from helpers import project


APP = QApplication.instance() or QApplication([])
theme.apply(APP, force="light")


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = project(Path(self.temp.name) / "示例项目")
        self.board_dir = Path(self.temp.name) / "board"
        self.env = patch.dict(os.environ, {
            "SCS_BOARD_DIR": str(self.board_dir),
            "XDG_STATE_HOME": str(Path(self.temp.name) / "state"),
        })
        self.env.start()
        self.board = Board(self.board_dir)
        self.first = self.board.add_task(str(self.root), "实现登录", "细节说明", "high",
                                         "2026-11-30", "session-one", "gui")
        self.second = self.board.add_task(str(self.root), "补测试", priority="low")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_board_snapshot_filters_project_and_counts(self):
        other = project(Path(self.temp.name) / "另一个项目")
        self.board.add_task(str(other), "别的项目任务")
        data = board_snapshot(self.root)
        self.assertTrue(data["available"])
        self.assertEqual({item["id"] for item in data["tasks"]},
                         {self.first["id"], self.second["id"]})
        self.assertEqual(data["counts"]["todo"], 2)
        self.assertEqual(data["counts"]["total"], 2)

    def test_board_snapshot_does_not_create_missing_dir(self):
        missing = Path(self.temp.name) / "not-created"
        with patch.dict(os.environ, {"SCS_BOARD_DIR": str(missing)}):
            data = board_snapshot(self.root)
        self.assertFalse(data["available"])
        self.assertEqual(data["tasks"], [])
        self.assertFalse(missing.exists())


class BoardWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = project(Path(self.temp.name) / "示例项目")
        self.board_dir = Path(self.temp.name) / "board"
        self.env = patch.dict(os.environ, {
            "SCS_BOARD_DIR": str(self.board_dir),
            "XDG_STATE_HOME": str(Path(self.temp.name) / "state"),
        })
        self.env.start()
        self.board = Board(self.board_dir)
        self.first = self.board.add_task(str(self.root), "实现登录", "细节说明", "high",
                                         "2026-11-30", "session-one", "gui")
        self.second = self.board.add_task(str(self.root), "补测试", priority="low")
        self.config = Path(self.temp.name) / "own/projects.json"
        self.window = MainWindow(self.config, initial_projects=[self.root], auto_load=False)
        self.window.poll.stop()
        self.window.theme_watcher.stop()
        self.window.projects.setCurrentRow(0)
        self.wait_idle()

    def tearDown(self):
        self.window.close()
        self.wait_idle()
        self.window.deleteLater()
        APP.processEvents()
        self.env.stop()
        self.temp.cleanup()

    def wait_idle(self):
        deadline = time.monotonic() + 8
        while self.window.busy and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertFalse(self.window.busy, self.window.status.text())

    def test_todo_tab_is_first_and_renders(self):
        self.assertEqual(self.window.tabs.tabText(0), "待办")
        self.assertEqual(self.window.tasks.rowCount(), 2)
        self.assertEqual(self.window.metric_todos.text(), "2")
        self.assertEqual(self.window.metric_active.text(), "0")
        self.assertFalse(self.window.task_hint.isVisible())

    def test_task_selection_prompt_and_buttons(self):
        self.window.tasks.selectRow(0)
        task = self.window.selected_task()
        self.assertIsNotNone(task)
        self.assertTrue(self.window.start_task_button.isEnabled())
        self.assertTrue(self.window.complete_task_button.isEnabled())
        self.window.copy_task_prompt()
        clipboard = QApplication.clipboard().text()
        self.assertIn(task["id"], clipboard)
        self.assertIn("board_task_claim", clipboard)
        self.assertIn(str(self.root), clipboard)

    def test_closed_tasks_hidden_by_default_then_shown(self):
        self.board.complete_task(self.first["id"], "gui", summary="done")
        from scs_manager.backend import snapshot
        self.window.apply_snapshot(snapshot(self.root))
        self.assertEqual(self.window.tasks.rowCount(), 1)
        self.window.show_closed_tasks.setChecked(True)
        self.assertEqual(self.window.tasks.rowCount(), 2)


class DashboardTests(unittest.TestCase):
    def test_project_cards_summary_and_filters(self):
        page = DashboardPage()
        self.addCleanup(page.deleteLater)
        now = time.time()
        entries = [
            {"root": "/p1", "name": "P1", "last_activity": now,
             "counts": {"in_progress": 2, "review_ready": 1, "todos": 3, "sessions": 4}},
            {"root": "/p2", "name": "P2", "last_activity": now - 3600,
             "counts": {"in_progress": 0, "review_ready": 0, "todos": 0, "sessions": 1}},
        ]
        page.set_data(entries, {"projects": {"/p1": 3}}, [])
        self.assertEqual(len(page.findChildren(ProjectCard)), 2)
        self.assertEqual(page.summary_labels["projects"].text(), "2")
        self.assertEqual(page.summary_labels["active"].text(), "2")
        self.assertEqual(page.summary_labels["review"].text(), "1")
        self.assertEqual(page.summary_labels["todos"].text(), "3")
        page.set_filter("active")
        self.assertEqual(len(page.findChildren(ProjectCard)), 1)
        page.set_filter("pinned")
        self.assertEqual(len(page.findChildren(ProjectCard)), 0)
        page.set_pinned(["/p2"])
        page.set_filter("pinned")
        self.assertEqual(len(page.findChildren(ProjectCard)), 1)

    def test_todo_page_filters_and_add_project(self):
        page = TodoPage()
        self.addCleanup(page.deleteLater)
        catalog = {
            "/p1": {"name": "P1", "sessions": [{"id": "s1", "client": "zcode", "mode": "standard"}]},
            "/p2": {"name": "P2", "sessions": []},
        }
        tasks = [
            {"id": "T1", "title": "高优", "state": "todo", "priority": "high",
             "project_root": "/p1", "project_name": "P1", "due_at": "2026-11-30",
             "updated_at": "2026-11-19T10:00:00+00:00"},
            {"id": "T2", "title": "进行中", "state": "in_progress", "priority": "medium",
             "project_root": "/p2", "project_name": "P2", "updated_at": "2026-11-19T09:00:00+00:00"},
            {"id": "T3", "title": "完成", "state": "done", "priority": "low",
             "project_root": "/p1", "project_name": "P1", "updated_at": "2026-11-18T09:00:00+00:00"},
        ]
        board = {"tasks": tasks, "counts": {"todo": 1, "claimed": 0, "in_progress": 1,
                                            "done": 1, "abandoned": 0}}
        page.set_data(board, catalog)
        self.assertEqual(len(page.findChildren(TodoCard)), 2)
        self.assertEqual(page.summary_labels["projects"].text(), "2")
        self.assertEqual(page.summary_labels["active"].text(), "1")
        page.set_filter("priority")
        self.assertEqual(len(page.findChildren(TodoCard)), 1)
        page.set_filter("active")
        self.assertEqual(len(page.findChildren(TodoCard)), 1)
        page.set_filter("done")
        self.assertEqual(len(page.findChildren(TodoCard)), 1)
        got = []
        page.add_task_requested.connect(got.append)
        page.set_filter("open")
        self.assertEqual(page.project_box.count(), 2)
        page.project_box.setCurrentIndex(page.project_box.findData("/p1"))
        page.request_add()
        self.assertEqual(got, ["/p1"])

    def test_task_dialog_session_is_in_form(self):
        dialog = TaskDialog([{"id": "session-one", "client": "zcode", "mode": "standard"}])
        self.addCleanup(dialog.deleteLater)
        form = None
        for index in range(dialog.layout().count()):
            layout = dialog.layout().itemAt(index).layout()
            if isinstance(layout, QFormLayout):
                form = layout
                break
        self.assertIsNotNone(form)
        self.assertEqual(form.rowCount(), 5)
        self.assertEqual(dialog.session.count(), 2)
        self.assertEqual(dialog.session.itemData(1), "session-one")
        with_project = TaskDialog([], project_root="/tmp/demo-project")
        self.addCleanup(with_project.deleteLater)
        form = None
        for index in range(with_project.layout().count()):
            layout = with_project.layout().itemAt(index).layout()
            if isinstance(layout, QFormLayout):
                form = layout
                break
        self.assertIsNotNone(form)
        self.assertEqual(form.rowCount(), 6)
        self.assertIn("demo-project", with_project.windowTitle())

    def test_todo_empty_state_and_dashboard_columns(self):
        todo = TodoPage()
        self.addCleanup(todo.deleteLater)
        todo.set_data({"tasks": [], "counts": {}}, {"/p1": {"name": "P1"}})
        self.assertEqual(todo.content.currentIndex(), 1)
        self.assertEqual(todo.empty_title.text(), "还没有待办")
        self.assertTrue(todo.empty_button.isEnabled())

        dashboard = DashboardPage()
        self.addCleanup(dashboard.deleteLater)
        entries = [{"root": f"/p{i}", "name": f"P{i}", "last_activity": 0,
                    "counts": {"in_progress": 0, "review_ready": 0, "todos": 0, "sessions": 0}}
                   for i in range(8)]
        dashboard.set_data(entries, {}, [])
        self.assertEqual(dashboard._columns_for(1450), 4)
        self.assertEqual(len(dashboard.findChildren(ProjectCard)), 8)
