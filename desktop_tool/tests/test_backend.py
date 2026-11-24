import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import scs_manager
from scs_manager import mcp_bridge, worker
from scs_manager.backend import (Registry, archive_view, database, discover, docs_root,
                                 inside, manifest, obsidian_target, session_uses_native_review,
                                 snapshot)
from helpers import project


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_uninitialized_inspection_creates_nothing(self):
        result = snapshot(self.root)
        self.assertEqual(result['database'], '未初始化')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_snapshot_keeps_db_bytes_schema_and_counts(self):
        project(self.root, schema=1)
        db = self.root / '.scs/state.sqlite3'
        before = db.read_bytes()
        data = snapshot(self.root)
        self.assertEqual(data['schema'], 1)
        self.assertEqual(data['counts']['work_packages'], 1)
        self.assertEqual(data['packages'][0]['state'], 'REVIEW_READY')
        self.assertEqual(data['host_connection'], '不可观测')
        self.assertEqual(db.read_bytes(), before)

    def test_docs_priority(self):
        (self.root / 'docs').mkdir()
        (self.root / '00_doc').mkdir()
        self.assertEqual(docs_root(self.root).name, '00_doc')

    def test_path_escape_and_symlink_refused(self):
        (self.root / 'link').symlink_to('/tmp')
        for path in ('../elsewhere', 'link', 'link/foo'):
            with self.assertRaises(ValueError):
                inside(self.root, path)

    def test_state_symlink_refused(self):
        (self.root / '.scs').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            snapshot(self.root)

    def test_foreign_db_and_unknown_schema_refused(self):
        project(self.root, schema=99)
        with self.assertRaisesRegex(ValueError, '版本'):
            snapshot(self.root)
        db = self.root / '.scs/state.sqlite3'
        con = sqlite3.connect(db)
        try:
            with con:
                con.execute('PRAGMA user_version=2')
                con.execute("UPDATE metadata SET value='/different'")
        finally:
            con.close()
        with self.assertRaisesRegex(ValueError, '另一项目'):
            snapshot(self.root)

    def test_bounded_discovery_ignores_symlinks(self):
        project(self.root / 'one')
        project(self.root / 'a/two')
        project(self.root / 'a/b/too-deep')
        (self.root / 'alias').symlink_to(self.root / 'one', target_is_directory=True)
        out = discover(self.root)
        self.assertEqual(len(out['projects']), 2)
        self.assertEqual(discover(self.root, maximum=1)['projects'], [])
        self.assertTrue(discover(self.root, maximum=1)['truncated'])

    def test_registry_roundtrip_and_no_write_on_load(self):
        registry = Registry(self.root / 'own/projects.json')
        self.assertEqual(registry.load()['projects'], [])
        self.assertFalse(registry.path.parent.exists())
        data = {'projects':[str(self.root / 'a 空格')], 'server_python':'/usr/bin/python3'}
        registry.save(data)
        self.assertEqual(registry.load(), data)
        self.assertEqual(list(registry.path.parent.iterdir()), [registry.path])

    def test_obsidian_encoded_note_and_nested_vault(self):
        project(self.root)
        nested = self.root / '子项目/docs'
        (nested / '.obsidian').mkdir(parents=True)
        note = nested / 'a #?.md'
        note.write_text('note')
        out = obsidian_target(self.root, note)
        self.assertEqual(out['vault_root'], str(nested))
        self.assertEqual(parse_qs(urlparse(out['uri']).query)['path'], [str(note)])
        self.assertIn('%23', out['uri'])

    def test_obsidian_no_note_uses_absolute_vault(self):
        (self.root / 'docs/.obsidian').mkdir(parents=True)
        out = obsidian_target(self.root)
        self.assertEqual(parse_qs(urlparse(out['uri']).query)['vault'], [str(self.root / 'docs')])

    def test_archive_preserves_every_byte_and_other_records(self):
        project(self.root)
        view = self.root / 'docs/agent/acceptance'
        (view / 'my extra.md').write_text('user extra')
        before = {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = archive_view(self.root)
        archive = Path(result['archive'])
        self.assertFalse(view.exists())
        for name, data in before.items():
            path = self.root / name
            if name.startswith('docs/agent/acceptance/'):
                path = archive / path.relative_to(view)
            self.assertEqual(path.read_bytes(), data, name)
        self.assertFalse(snapshot(self.root)['view']['auto_refresh'])
        archive.rename(view)
        self.assertTrue(snapshot(self.root)['view']['auto_refresh'])

    def test_archive_unowned_and_symlink_refused(self):
        project(self.root)
        view = self.root / 'docs/agent/acceptance'
        (view / 'alias').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            archive_view(self.root)
        self.assertTrue(view.exists())
        (view / 'alias').unlink()
        (view / '.scs-view.json').unlink()
        with self.assertRaisesRegex(ValueError, '没有'):
            archive_view(self.root)

    def test_bad_manifest_refused(self):
        project(self.root)
        path = self.root / 'docs/agent/acceptance/.scs-view.json'
        data = json.loads(path.read_text())
        data['files']['../../outside.md'] = ['a'*64]
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            manifest(self.root)

    def test_archive_serializes_with_mcp_writer(self):
        project(self.root)
        with database(self.root, write=True):
            with self.assertRaises(sqlite3.OperationalError):
                archive_view(self.root)
        self.assertTrue((self.root / 'docs/agent/acceptance').is_dir())

    def test_catalog_uses_latest_event_without_touching_database(self):
        from scs_manager.backend import project_catalog
        first=project(self.root/'first');second=project(self.root/'second')
        db=second/'.scs/state.sqlite3';con=sqlite3.connect(db)
        with con:con.execute("UPDATE events SET at='2026-11-11T12:00:00Z'")
        con.close();before=db.read_bytes()
        result=project_catalog([str(first),str(second)])
        self.assertGreater(result[1]['last_activity'],result[0]['last_activity'])
        self.assertEqual(result[0]['packages'][0]['id'],'WP-demo')
        self.assertEqual(db.read_bytes(),before)

    def test_catalog_hides_closed_packages(self):
        from scs_manager.backend import project_catalog
        root = project(self.root / 'closed')
        with sqlite3.connect(root / '.scs/state.sqlite3') as con:
            con.execute("UPDATE work_packages SET state='ABANDONED' WHERE id='WP-demo'")
        self.assertEqual(project_catalog([str(root)])[0]['packages'], [])

    def test_catalog_missing_project_is_retained_as_unavailable(self):
        from scs_manager.backend import project_catalog
        out=project_catalog([str(self.root/'missing')])
        self.assertEqual(len(out),1)
        self.assertTrue(out[0]['error'])

    def test_registry_rejects_invalid_sidebar_preferences(self):
        registry=Registry(self.root/'projects.json')
        registry.path.write_text(json.dumps({'projects':[],'last_opened':{'a':'yesterday'}}))
        with self.assertRaises(ValueError):registry.load()

    def test_version_matches_repo_version(self):
        repo_version = (Path(__file__).resolve().parents[2] / 'VERSION').read_text(encoding='utf-8').splitlines()[0].strip()
        self.assertEqual(scs_manager.__version__, '3.0.0')
        self.assertEqual(scs_manager.__version__, repo_version)

    def test_session_rows_reads_codex_gate_review_route_and_native_review(self):
        root = project(self.root / 'native-sessions')
        zcode_none_hash = 'h' + hashlib.sha256(
            json.dumps(['zcode', None], ensure_ascii=False, separators=(',', ':')).encode()
        ).hexdigest()[:32]
        zcode_empty_hash = 'h' + hashlib.sha256(
            json.dumps(['zcode', ''], ensure_ascii=False, separators=(',', ':')).encode()
        ).hexdigest()[:32]
        zcode_pair_hash = 'h' + hashlib.sha256(
            json.dumps(['zcode', 'zcode'], ensure_ascii=False, separators=(',', ':')).encode()
        ).hexdigest()[:32]
        with sqlite3.connect(root / '.scs/state.sqlite3') as con:
            con.executescript('''
                CREATE TABLE questions(
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL,
                    current INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL,
                    channel TEXT NOT NULL, recommendation TEXT NOT NULL, answer TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
            ''')
            con.execute(
                'INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                ('S-long-native', zcode_pair_hash, 'long', '["targeted"]', None, 'registered', '2026-11-20T01:00:00Z'),
            )
            con.execute(
                'INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                ('S-std-zcode', zcode_empty_hash, 'standard', '["targeted"]', None, 'registered', '2026-11-20T02:00:00Z'),
            )
            con.execute(
                'INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                ('S-std-nonehash', zcode_none_hash, 'standard', '["smoke"]', None, 'registered', '2026-11-20T03:00:00Z'),
            )
            con.execute(
                'INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                ('S-route-only', 'custom-host', 'long', '["targeted"]', None, 'unknown', '2026-11-20T04:00:00Z'),
            )
            con.execute(
                'INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('Q-gate-1', 'S-long-native', 'codex_gate', 1, 'CONFIRMED', 'native', '["packages"]', '["packages"]',
                 '2026-11-20T01:00:01Z', '2026-11-20T01:00:02Z'),
            )
            con.execute(
                'INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('Q-route-1', 'S-long-native', 'review_route', 1, 'CONFIRMED', 'native', '["gpt"]', '["gpt"]',
                 '2026-11-20T01:00:03Z', '2026-11-20T01:00:04Z'),
            )
            con.execute(
                'INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('Q-gate-2', 'S-route-only', 'codex_gate', 1, 'CONFIRMED', 'native', '["closure"]', '["closure"]',
                 '2026-11-20T04:00:01Z', '2026-11-20T04:00:02Z'),
            )
            con.execute(
                'INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('Q-route-2', 'S-route-only', 'review_route', 1, 'CONFIRMED', 'native', '["inherit"]', '["inherit"]',
                 '2026-11-20T04:00:03Z', '2026-11-20T04:00:04Z'),
            )
        clean_env = {k: v for k, v in os.environ.items() if k != 'SCS_NATIVE_REVIEW'}
        with patch.dict(os.environ, clean_env, clear=True):
            sessions = {s['id']: s for s in snapshot(root)['sessions']}
            self.assertEqual(sessions['S-long-native']['codex_gate'], 'packages')
            self.assertEqual(sessions['S-long-native']['review_route'], 'gpt')
            self.assertTrue(sessions['S-long-native']['native_review'])
            self.assertIsNone(sessions['S-std-zcode']['codex_gate'])
            self.assertIsNone(sessions['S-std-zcode']['review_route'])
            self.assertTrue(sessions['S-std-zcode']['native_review'])
            self.assertTrue(sessions['S-std-nonehash']['native_review'])
            self.assertEqual(sessions['S-route-only']['codex_gate'], 'closure')
            self.assertEqual(sessions['S-route-only']['review_route'], 'inherit')
            self.assertTrue(sessions['S-route-only']['native_review'])
            self.assertIsNone(sessions['session-one']['codex_gate'])
            self.assertIsNone(sessions['session-one']['review_route'])
            self.assertFalse(sessions['session-one']['native_review'])
        with patch.dict(os.environ, {**clean_env, 'SCS_NATIVE_REVIEW': '1'}, clear=True):
            sessions_env = {s['id']: s for s in snapshot(root)['sessions']}
            self.assertTrue(sessions_env['session-one']['native_review'])

    def test_mcp_bridge_forwards_native_review_env_and_flag(self):
        parsed_default = mcp_bridge.parse_args(['refresh', '--root', str(self.root), '--session', 'S-1'])
        self.assertFalse(parsed_default.native_review)
        parsed_flag = mcp_bridge.parse_args(['refresh', '--root', str(self.root), '--session', 'S-1', '--native-review'])
        self.assertTrue(parsed_flag.native_review)

        env_plain = mcp_bridge.build_server_env(native_review=False, environ={'PATH': '/usr/bin'})
        self.assertNotIn('SCS_NATIVE_REVIEW', env_plain)
        env_forwarded = mcp_bridge.build_server_env(native_review=False, environ={'SCS_NATIVE_REVIEW': '1'})
        self.assertEqual(env_forwarded['SCS_NATIVE_REVIEW'], '1')
        env_flag = mcp_bridge.build_server_env(native_review=True, environ={'PATH': '/usr/bin'})
        self.assertEqual(env_flag['SCS_NATIVE_REVIEW'], '1')

        captured = []

        class FakeParams:
            def __init__(self, command, args, env):
                self.command = command
                self.args = args
                self.env = env
                captured.append(env)

        class FakeClient:
            def __init__(self, params, read_timeout_seconds=8):
                self.params = params

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def call_tool(self, name, arguments):
                return types.SimpleNamespace(
                    is_error=False,
                    structured_content={'status': 'OK', 'tool': name, 'arguments': arguments},
                )

        fake_mcp = types.ModuleType('mcp')
        fake_mcp.Client = FakeClient
        fake_mcp_client = types.ModuleType('mcp.client')
        fake_mcp_stdio = types.ModuleType('mcp.client.stdio')
        fake_mcp_stdio.StdioServerParameters = FakeParams

        clean_env = {k: v for k, v in os.environ.items() if k != 'SCS_NATIVE_REVIEW'}
        with patch.dict(sys.modules, {'mcp': fake_mcp, 'mcp.client': fake_mcp_client,
                                      'mcp.client.stdio': fake_mcp_stdio}):
            with patch.dict(os.environ, clean_env, clear=True):
                asyncio.run(mcp_bridge.run(parsed_flag))
            with patch.dict(os.environ, {**clean_env, 'SCS_NATIVE_REVIEW': '1'}, clear=True):
                asyncio.run(mcp_bridge.run(parsed_default))
            with patch.dict(os.environ, clean_env, clear=True):
                asyncio.run(mcp_bridge.run(parsed_default))

        self.assertEqual(captured[0].get('SCS_NATIVE_REVIEW'), '1')
        self.assertEqual(captured[1].get('SCS_NATIVE_REVIEW'), '1')
        self.assertNotIn('SCS_NATIVE_REVIEW', captured[2])

    def test_worker_refresh_passes_native_review_only_when_applicable(self):
        root = project(self.root / 'worker-native')
        zcode_empty_hash = 'h' + hashlib.sha256(
            json.dumps(['zcode', ''], ensure_ascii=False, separators=(',', ':')).encode()
        ).hexdigest()[:32]
        with sqlite3.connect(root / '.scs/state.sqlite3') as con:
            con.executescript('''
                CREATE TABLE questions(
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL,
                    current INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL,
                    channel TEXT NOT NULL, recommendation TEXT NOT NULL, answer TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
            ''')
            con.execute(
                'INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                ('S-std-zcode', zcode_empty_hash, 'standard', '["targeted"]', None, 'registered', '2026-11-20T01:00:00Z'),
            )
            con.execute(
                'INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                ('S-std-legacy-pending', 'codex', 'standard', '["targeted"]', None, 'registered', '2026-11-20T01:30:00Z'),
            )
            con.execute(
                'INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                ('S-long-route', 'custom-hash', 'long', '["targeted"]', None, 'registered', '2026-11-20T02:00:00Z'),
            )
            con.execute(
                'INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('Q-r', 'S-long-route', 'review_route', 1, 'CONFIRMED', 'native', '["gpt"]', '["gpt"]',
                 '2026-11-20T02:00:01Z', '2026-11-20T02:00:02Z'),
            )

        def run_worker_refresh(session_id, extra_env=None):
            clean_env = {k: v for k, v in os.environ.items() if k != 'SCS_NATIVE_REVIEW'}
            if extra_env:
                clean_env.update(extra_env)
            fake_completed = MagicMock(stdout=json.dumps({'ok': True, 'data': {'result': 'OK'}}).encode())
            with patch.dict(os.environ, clean_env, clear=True), \
                 patch('scs_manager.worker.os.setsid'), \
                 patch('scs_manager.worker.subprocess.run', return_value=fake_completed) as mock_run, \
                 patch.object(sys, 'argv', ['worker', 'refresh', '--root', str(root), '--session', session_id]), \
                 patch('sys.stdout', new_callable=io.StringIO):
                code = worker.main()
            self.assertEqual(code, 0)
            return mock_run.call_args[0][0]

        cmd_legacy = run_worker_refresh('session-one')
        self.assertNotIn('--native-review', cmd_legacy)

        cmd_legacy_std = run_worker_refresh('S-std-legacy-pending')
        self.assertNotIn('--native-review', cmd_legacy_std)

        cmd_std = run_worker_refresh('S-std-zcode')
        self.assertIn('--native-review', cmd_std)

        cmd_route = run_worker_refresh('S-long-route')
        self.assertIn('--native-review', cmd_route)

        cmd_env = run_worker_refresh('session-one', {'SCS_NATIVE_REVIEW': '1'})
        self.assertIn('--native-review', cmd_env)

        with database(root) as con:
            with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != 'SCS_NATIVE_REVIEW'}, clear=True):
                self.assertFalse(session_uses_native_review(con, 'session-one'))
                self.assertFalse(session_uses_native_review(con, 'S-std-legacy-pending'))
                self.assertTrue(session_uses_native_review(con, 'S-std-zcode'))
                self.assertTrue(session_uses_native_review(con, 'S-long-route'))
