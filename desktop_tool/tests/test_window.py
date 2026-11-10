import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QProcess, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox
from scs_manager import theme
from scs_manager.backend import Registry
from scs_manager.window import MainWindow
from helpers import project

APP = QApplication.instance() or QApplication([])
theme.apply(APP, force='light')


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = project(Path(self.temp.name) / '示例项目')
        self.config = Path(self.temp.name) / 'own/projects.json'
        self.window = MainWindow(self.config, initial_projects=[self.root], auto_load=False)
        self.window.poll.stop()
        self.window.theme_watcher.stop()

    def tearDown(self):
        self.window.close()
        self.wait_idle()
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def wait_idle(self):
        deadline = time.monotonic() + 8
        while self.window.busy and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertFalse(self.window.busy, self.window.status.text())

    def load(self):
        self.window.projects.setCurrentRow(0)
        self.wait_idle()
        self.assertFalse(self.window.last_error, self.window.last_error)

    def test_real_async_worker_loads_without_ui_blocking(self):
        self.load()
        self.assertEqual(self.window.packages.rowCount(), 1)
        self.assertEqual(self.window.managed_files.count(), 1)
        self.assertEqual(self.window.sessions.currentData(), 'session-one')
        self.assertIn('可读取', self.window.summary.text())
        self.assertIn('不可观测', self.window.connection.text())
        self.assertFalse(self.config.exists())

    def test_filter_and_remove_never_delete_project(self):
        self.load()
        self.window.search.setText('absent')
        self.assertTrue(self.window.projects.item(0).isHidden())
        self.window.search.clear()
        self.window.remove_project()
        self.assertTrue((self.root / '.scs/state.sqlite3').is_file())
        self.assertEqual(Registry(self.config).load()['projects'], [])
        self.assertFalse(self.window.tree.isEnabled())

    def test_single_job_and_visible_only_poll(self):
        self.load()
        self.window.start_job('snapshot')
        pid = self.window.job.processId()
        self.window.start_job('probe')
        self.assertEqual(self.window.action, 'snapshot')
        self.wait_idle()
        self.window.hide()
        self.window.poll_snapshot()
        self.assertFalse(self.window.busy)

    def test_open_uri_and_directory_callbacks_do_not_execute_files(self):
        self.load()
        with patch('scs_manager.window.QDesktopServices.openUrl', return_value=True) as opened:
            self.window.open_docs()
            self.assertEqual(opened.call_args[0][0].toLocalFile(), str(self.root / 'docs'))
            with patch('scs_manager.window.QProcess.startDetached',return_value=(True,1234)) as launched:
                self.window.open_obsidian()
                self.wait_idle()
                self.assertFalse(self.window.last_error,self.window.last_error)
                self.assertTrue(any(a.startswith('--user-data-dir=') for a in launched.call_args[0][1]))
                self.assertTrue(launched.call_args[0][1][-1].startswith('obsidian://open?'))
                self.assertIn('首次配置已自动准备',self.window.status.text())
                self.assertEqual((self.root/'docs/.obsidian/app.json').read_text(),'{"user":true}')

    def test_archive_cancel_is_no_write(self):
        self.load()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            self.window.archive_view()
        self.assertTrue((self.root / 'docs/agent/acceptance').is_dir())
        self.assertFalse(self.window.busy)

    def test_archive_confirm_real_worker_preserves_extra_note(self):
        self.load()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.window.archive_view()
        self.wait_idle()
        QTest.qWait(30)
        self.wait_idle()
        self.assertFalse(self.window.last_error, self.window.last_error)
        self.assertEqual(len(list((self.root / 'docs/agent/view-archives').iterdir())), 1)
        self.assertFalse((self.root / 'docs/agent/acceptance').exists())

    def test_probe_failure_has_no_online_claim(self):
        self.load()
        self.window.connection.setText('自检成功 · 历史结果')
        self.window.settings['server_python'] = '/nonexistent/python'
        self.window.start_job('probe')
        self.wait_idle()
        self.assertIn('操作未完成', self.window.status.text())
        self.assertIn('不可观测', self.window.connection.text())
        self.assertIn('自检失败', self.window.connection.text())
        self.assertTrue(self.window.projects.isEnabled())

    def test_small_window_theme_and_screenshots(self):
        self.load()
        from scs_manager.backend import project_catalog
        other=project(Path(self.temp.name)/'另一个项目')
        self.window.settings['projects'].append(str(other))
        self.window.settings['collapsed_projects']={str(other):True}
        entries=project_catalog(self.window.settings['projects'])
        entries[0]['last_activity']+=100
        self.window.catalog={e['root']:e for e in entries}
        self.window.rebuild_projects()
        target = os.environ.get('SCS_SCREENSHOT_DIR')
        for scheme, index in (('light', 1), ('dark', 2)):
            self.window.theme_box.setCurrentIndex(index)
            self.window.resize(1220, 820)
            self.window.show()
            QTest.qWait(80)
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.window.grab().save(str(Path(target) / (scheme + '.png')))
            self.window.resize(800, 600)
            QTest.qWait(30)
            if target:
                self.window.grab().save(str(Path(target) / (scheme + '-small.png')))
            self.assertLessEqual(self.window.width(), 820)
            for page in (1, 2, 3):
                self.window.tabs.setCurrentIndex(page)
                QTest.qWait(20)
                if target:
                    self.window.grab().save(str(Path(target) / f'{scheme}-tab{page}-small.png'))
            self.window.tabs.setCurrentIndex(0)

    def test_close_reaps_owned_job(self):
        self.load()
        self.window.start_job('snapshot')
        self.window.close()
        self.wait_idle()
        QTest.qWait(30)
        self.assertEqual(self.window.job.state(), QProcess.NotRunning)

    def test_sidebar_folds_persist_and_activity_reorders_without_losing_selection(self):
        self.load()
        from scs_manager.backend import project_catalog
        second=project(Path(self.temp.name)/'第二项目')
        self.window.settings['projects'].append(str(second))
        entries=project_catalog(self.window.settings['projects'])
        entries[0]['last_activity']=100
        entries[1]['last_activity']=200
        self.window.catalog={e['root']:e for e in entries}
        self.window.rebuild_projects()
        self.assertEqual(self.window.projects.item(0).data(0,Qt.UserRole),str(second))
        current=self.window.projects.currentItem()
        self.assertEqual(current.data(0,Qt.UserRole),str(self.root))
        self.assertEqual(current.childCount(),1)
        current.setExpanded(False)
        self.assertTrue(Registry(self.config).load()['collapsed_projects'][str(self.root)])
        entries[0]['last_activity']=300
        self.window.rebuild_projects()
        self.assertEqual(self.window.projects.item(0).data(0,Qt.UserRole),str(self.root))
        self.assertFalse(current.isExpanded())
        current.setExpanded(True)
        self.window.projects.setCurrentItem(current.child(0))
        self.assertEqual(self.window.tabs.currentIndex(),1)
        self.assertEqual(self.window.packages.currentRow(),0)

    def test_sidebar_catalog_reads_real_worker(self):
        self.load()
        self.window.refresh_catalog()
        deadline=time.monotonic()+5
        while self.window.catalog_job.state()!=QProcess.NotRunning and time.monotonic()<deadline:
            QTest.qWait(20)
        self.assertEqual(self.window.projects.item(0).childCount(),1)
        self.assertFalse(self.window.last_error,self.window.last_error)

    def test_failed_detached_launch_reports_error(self):
        self.load()
        with patch('scs_manager.window.QProcess.startDetached',return_value=(False,0)):
            self.window.open_obsidian();self.wait_idle()
        self.assertIn('启动失败',self.window.status.text())

    def test_detached_launch_accepts_pyqt_boolean_return(self):
        self.load()
        with patch('scs_manager.window.QProcess.startDetached',return_value=True):
            self.window.open_obsidian();self.wait_idle()
        self.assertFalse(self.window.last_error,self.window.last_error)
        self.assertIn('首次配置已自动准备',self.window.status.text())
