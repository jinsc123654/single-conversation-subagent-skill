"""Explicit integration probe: system PyQt GUI -> worker -> SDK2 client -> real stdio MCP.

Temporary project only; no global config or external app launch.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox
from scs_manager import theme
from scs_manager.window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server-python', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    app = QApplication([])
    theme.apply(app, force='light')
    results = []
    with tempfile.TemporaryDirectory(prefix='scs-live-gui-') as temporary:
        root = Path(temporary) / '项目 #?.test'
        root.mkdir()
        fixture = '''from scs_mcp.storage import Store
from scs_mcp.state import State
import sys
s=Store(sys.argv[1]); row=State(s).open(str(s.root),"desktop-integration-fixture",explicit_skip=True)
print(row["id"])
'''
        seed = subprocess.run([args.server_python, '-c', fixture, str(root)],
            env={**os.environ, 'PYTHONPATH': str(repository / 'mcp_server/src')}, capture_output=True, text=True, timeout=10)
        assert seed.returncode == 0, seed.stderr
        window = MainWindow(Path(temporary) / 'config/projects.json', initial_projects=[root], auto_load=False)
        window.settings['server_python'] = args.server_python
        window.poll.stop()
        window.theme_watcher.stop()
        def wait():
            deadline = time.monotonic() + 35
            while window.busy and time.monotonic() < deadline:
                QTest.qWait(20)
            assert not window.busy, 'GUI process did not terminate'
            assert not window.last_error, window.last_error
        window.projects.setCurrentRow(0)
        wait()
        assert window.sessions.currentData() == seed.stdout.strip()
        results.append('snapshot:PASS')
        window.start_job('probe')
        wait()
        assert '17 个工具' in window.connection.text(), window.connection.text()
        results.append('real-stdio-probe-17-tools:PASS')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            window.refresh_view()
        wait(); QTest.qWait(30); wait()
        home = root / 'docs/agent/acceptance/00_项目总览.md'
        assert home.is_file() and (root / 'docs/.obsidian/app.json').is_file()
        home.write_text(home.read_text().replace('<!-- manual:begin -->', '<!-- manual:begin -->\nGUI integration annotation'))
        window.start_job('refresh'); wait(); QTest.qWait(30); wait()
        assert 'GUI integration annotation' in home.read_text()
        results.append('real-mcp-refresh-and-preserve-annotation:PASS')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            window.archive_view()
        wait(); QTest.qWait(30); wait()
        archives = list((root / 'docs/agent/view-archives').iterdir())
        assert len(archives) == 1 and not home.exists()
        assert 'GUI integration annotation' in (archives[0] / home.name).read_text()
        results.append('real-archive:PASS')
        window.start_job('refresh'); wait(); QTest.qWait(30); wait()
        assert home.is_file()
        assert 'GUI integration annotation' in (archives[0] / home.name).read_text()
        results.append('refresh-after-archive:PASS')
        window.close(); QTest.qWait(30)
        assert not (Path(temporary) / 'config/projects.json').exists()
        results.append('no-global-install-or-config-write:PASS')
    data = {'status':'PASS','results':results,'gui':'offscreen PyQt5; external applications NOT_TESTED'}
    Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(data, ensure_ascii=False))


if __name__ == '__main__':
    main()
