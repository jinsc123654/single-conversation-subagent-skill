import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from scs_manager.backend import (Registry, archive_view, database, discover, docs_root,
                                 inside, manifest, obsidian_target, snapshot)
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

    def test_catalog_missing_project_is_retained_as_unavailable(self):
        from scs_manager.backend import project_catalog
        out=project_catalog([str(self.root/'missing')])
        self.assertEqual(len(out),1)
        self.assertTrue(out[0]['error'])

    def test_registry_rejects_invalid_sidebar_preferences(self):
        registry=Registry(self.root/'projects.json')
        registry.path.write_text(json.dumps({'projects':[],'last_opened':{'a':'yesterday'}}))
        with self.assertRaises(ValueError):registry.load()
