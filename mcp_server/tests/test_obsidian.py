import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from scs_mcp.errors import ScsError
from scs_mcp.obsidian import BEGIN, END, PAGES, ProjectView
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work


@pytest.fixture
def project(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test-host", "one", explicit_skip=True)["id"]
    return store, sid, Work(store), ProjectView(store)


def package(project, key="one", goal="显示中文标题"):
    store, sid, work, _ = project
    return work.create(sid, goal, ["src"], ["保持现有功能"],
        [{"id": "T1", "kind": "test", "allowed_files": ["src/file.py"], "acceptance": ["定向验证"]}], key)


def pass_task(project, p):
    store, sid, work, _ = project
    path = f"{p['docs_root']}/agent/evidence/{p['id']}/R{p['revision']}/T1/result.json"
    (store.root / path).write_text(json.dumps({"command": "pytest", "exit_code": 0, "passed": 1}))
    check = work.evidence(sid, p["id"], p["revision"], "T1", path)
    p = work.update(sid, p["id"], p["version"], "IN_PROGRESS", [
        {"id": "T1", "status": "PASS", "evidence": [{"path": path, "sha256": check["sha256"]}]}])
    return p, path


@pytest.mark.parametrize("docs", ["00_doc", "00_docs", "docs", "doc", None])
def test_info_is_read_only_and_refresh_uses_existing_docs_root(project, docs):
    store, sid, _, view = project
    if docs:
        (store.root / docs).mkdir()
    before = sorted(p.relative_to(store.root) for p in store.root.rglob("*"))
    info = view.info(sid)
    assert info["docs_root"] == (docs or "docs")
    assert not info["view_initialized"] and not info["local_config_present"]
    assert sorted(p.relative_to(store.root) for p in store.root.rglob("*")) == before
    out = view.refresh(sid, initialize_vault=True)
    assert out["docs_root"] == (docs or "docs")
    assert out["local_config_present"] and not out["needs_refresh"]
    assert not (store.root / "docs/docs").exists()
    assert (Path(out["vault_root"]) / ".obsidian/app.json").read_text() == "{}\n"
    assert out["vault_registration"] == "NOT_OBSERVABLE" and not out["application_opened"]
    assert not out["plugin_required"]


def test_existing_obsidian_config_and_memory_are_preserved(project):
    store, sid, _, view = project
    root = store.root / "docs"
    (root / ".obsidian").mkdir(parents=True)
    config = root / ".obsidian/app.json"
    config.write_text('{"user-settings": [1,2], "custom": "keep exact whitespace"}\n')
    (root / "agent").mkdir()
    memory = root / "agent/AGENT_PROJECT.md"
    memory.write_text("MY PROJECT NOTES\n")
    original = config.read_bytes(), memory.read_bytes()
    view.refresh(sid, True)
    assert (config.read_bytes(), memory.read_bytes()) == original
    assert not (root / ".obsidian/community-plugins.json").exists()
    assert not (root / ".obsidian/plugins").exists()


def test_grouping_revision_history_and_links(project):
    store, sid, work, view = project
    p = package(project)
    p, _ = pass_task(project, p)
    p = work.update(sid, p["id"], p["version"], "REVIEW_READY")
    blocked = package(project, "blocked", "等待修复")
    work.update(sid, blocked["id"], 1, "BLOCKED", [{"id": "T1", "status": "FAIL"}])
    out = view.refresh(sid)
    assert out["groups"]["attention"] == 1 and out["groups"]["review"] == 1
    assert out["groups"]["complete"] == 1
    home = store.root / out["overview_path"]
    assert p["id"] not in home.read_text()  # two-level MOC, no star of every WP
    assert "最终验收" in (home.parent / PAGES["complete"]).read_text()
    p = work.update(sid, p["id"], p["version"], new_revision=True)
    assert view.info(sid)["snapshot_stale"]
    out = view.refresh(sid)
    r1 = home.parent / f"revisions/{p['id']}-R1.md"
    r2 = home.parent / f"revisions/{p['id']}-R2.md"
    assert "已报告通过" in r1.read_text() and "未运行" in r2.read_text()
    assert out["groups"]["complete"] == 0 and out["revisions"] == 3
    # All generated relative links in this fixture resolve to files in the vault.
    for note in home.parent.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", note.read_text()):
            resolved = (note.parent / unquote(target)).resolve()
            assert resolved.is_relative_to(store.root / "docs"), (note, target)
            assert resolved.exists(), (note, target)


def test_manual_annotations_preserved_and_refresh_idempotent(project):
    store, sid, work, view = project
    p = package(project)
    out = view.refresh(sid)
    page = store.root / out["overview_path"]
    manual = "\n我的批注：不要覆盖。\n\n- [ ] 自己的计划\n"
    page.write_text(page.read_text().replace(BEGIN + "\n\n" + END, BEGIN + manual + END))
    no_changes = view.refresh(sid)
    assert no_changes["updated_files"] == 0
    assert BEGIN + manual + END in page.read_text()
    assert not view.info(sid)["generated_content_drift"]
    work.update(sid, p["id"], 1, "IN_PROGRESS")
    view.refresh(sid)
    assert BEGIN + manual + END in page.read_text()


@pytest.mark.parametrize("edit", ["generated", "missing-marker", "nested-marker", "reversed"])
def test_manual_conflict_stops_before_any_projection_updates(project, edit):
    store, sid, work, view = project
    p = package(project)
    out = view.refresh(sid)
    page = store.root / out["overview_path"]
    original = page.read_text()
    replacements = {"generated": original.replace("按状态查看", "用户改了生成段"),
        "missing-marker": original.replace(END, ""), "nested-marker": original + BEGIN,
        "reversed": original.replace(BEGIN, "TEMP").replace(END, BEGIN).replace("TEMP", END)}
    page.write_text(replacements[edit])
    work.update(sid, p["id"], 1, "IN_PROGRESS")
    before = {p: p.read_bytes() for p in page.parent.rglob("*") if p.is_file()}
    with pytest.raises(ScsError) as err:
        view.refresh(sid, True)
    assert err.value.code == "VIEW_CONFLICT"
    assert {p: p.read_bytes() for p in page.parent.rglob("*") if p.is_file()} == before
    assert not (store.root / "docs/.obsidian").exists()


def test_unmanaged_existing_notes_are_never_adopted_or_overwritten(project):
    store, sid, _, view = project
    target = store.root / "docs/agent/acceptance" / PAGES["home"]
    target.parent.mkdir(parents=True)
    target.write_text("Existing project dashboard\n")
    with pytest.raises(ScsError):
        view.refresh(sid, True)
    assert target.read_text() == "Existing project dashboard\n"
    assert sorted(p.name for p in target.parent.iterdir()) == [PAGES["home"]]
    assert not (store.root / "docs/agent/AGENT_PROJECT.md").exists()


@pytest.mark.parametrize("component", ["docs", "docs/.obsidian", "docs/agent/acceptance", "docs/agent/acceptance/.scs-view.json"])
def test_view_paths_cannot_follow_symlinks(project, tmp_path, component):
    store, sid, _, view = project
    outside = tmp_path / "outside"
    outside.mkdir()
    path = store.root / component
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)
    with pytest.raises(ScsError):
        view.refresh(sid, True)
    assert list(outside.iterdir()) == []


def test_uri_handles_spaces_unicode_and_same_vault_folder_name(tmp_path):
    roots = [tmp_path / "项目 A & B" / "repo", tmp_path / "other" / "repo"]
    uris = []
    for root in roots:
        root.mkdir(parents=True)
        store = Store(root)
        sid = State(store).open(str(root), "host", explicit_skip=True)["id"]
        out = ProjectView(store).refresh(sid)
        uri = out["open_uri"]
        decoded = parse_qs(urlparse(uri).query)
        assert decoded["path"] == [str(root / out["overview_path"])]
        assert " " not in uri and "项目" not in uri and "& B" not in uri
        uris.append(uri)
    assert uris[0] != uris[1]


def test_tampered_evidence_moves_to_attention_and_refresh_stops_being_stale(project):
    store, sid, _, view = project
    p, path = pass_task(project, package(project))
    view.refresh(sid)
    (store.root / path).write_text("tampered")
    assert view.info(sid)["evidence_stale"]
    out = view.refresh(sid)
    assert out["groups"]["attention"] == 1 and out["groups"]["complete"] == 0
    assert out["evidence_issues"] and not out["needs_refresh"]
    page = store.root / out["view_root"] / "work-packages" / (p["id"] + ".md")
    assert "证据需重新核对" in page.read_text()
    (store.root / path).unlink()
    (store.root / path).symlink_to("/etc/passwd")
    assert view.refresh(sid)["groups"]["attention"] == 1
    assert "路径需核对" in page.read_text()


def test_interrupted_refresh_recovers_own_partial_output(project, monkeypatch):
    store, sid, work, view = project
    p = package(project)
    original = store.paths.replace_checked
    calls = [0]
    def crash(path, data, expected):
        original(path, data, expected)
        calls[0] += 1
        if calls[0] == 3:
            raise RuntimeError("simulated interruption")
    monkeypatch.setattr(store.paths, "replace_checked", crash)
    with pytest.raises(RuntimeError):
        view.refresh(sid)
    monkeypatch.setattr(store.paths, "replace_checked", original)
    assert view.info(sid)["refresh_incomplete"]
    # DB may have advanced while a previous output was only partly written.
    work.update(sid, p["id"], 1, "IN_PROGRESS")
    out = view.refresh(sid)
    assert not out["refresh_incomplete"] and not out["needs_refresh"]
    assert view.refresh(sid)["updated_files"] == 0


def test_manifest_corruption_is_read_only_failure(project):
    store, sid, _, view = project
    out = view.refresh(sid)
    manifest = store.root / out["view_root"] / ".scs-view.json"
    manifest.write_text('{"bad": true}')
    before = {p: p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file()}
    with pytest.raises(ScsError) as err:
        view.refresh(sid)
    assert err.value.code == "VIEW_MANIFEST_INVALID"
    assert {p: p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file()} == before


def test_migrate_schema1_preserves_sessions_packages_and_only_current_revision(project):
    store, sid, work, _ = project
    p = package(project)
    with sqlite3.connect(store.db) as con:
        con.execute("DROP TABLE work_package_revisions")
        con.execute("UPDATE work_packages SET revision=3 WHERE id=?", (p["id"],))
        con.execute("PRAGMA user_version=1")
        original = con.execute("SELECT * FROM work_packages").fetchall()
        sessions = con.execute("SELECT * FROM sessions").fetchall()
    migrated = Store(store.root)
    with migrated.transaction() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert [tuple(r) for r in con.execute("SELECT * FROM work_packages")] == original
        assert [tuple(r) for r in con.execute("SELECT * FROM sessions")] == sessions
        assert [r[0] for r in con.execute("SELECT revision FROM work_package_revisions")] == [3]
    out = ProjectView(migrated).refresh(sid)
    page = store.root / out["view_root"] / "work-packages" / (p["id"] + ".md")
    assert "早期修订没有结构化快照" in page.read_text()
    assert not (page.parent.parent / "revisions" / (p["id"] + "-R1.md")).exists()


def test_parallel_refresh_serializes_and_preserves_annotations(project):
    store, sid, _, view = project
    package(project)
    out = view.refresh(sid)
    page = store.root / out["overview_path"]
    page.write_text(page.read_text().replace(BEGIN, BEGIN + "\nKEEP\n"))
    def refresh(_):
        return ProjectView(Store(store.root)).refresh(sid)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(refresh, range(4)))
    assert all(r["updated_files"] == 0 for r in results)
    assert "KEEP" in page.read_text()


def test_goals_are_escaped_and_old_docs_records_stay_outside_new_vault(project):
    store, sid, _, view = project
    p = package(project, goal="![remote](https://example.com/image) <!-- manual:begin -->")
    (store.root / "00_doc").mkdir()
    out = view.refresh(sid)
    assert out["sources_outside_vault"] == ["docs"]
    page = store.root / out["view_root"] / "work-packages" / (p["id"] + ".md")
    body = page.read_text()
    assert body.count(BEGIN) == 1 and "&lt;" in body and "库外" in body
    assert not re.search(r"(?<!\\)!\[", body)


def test_checked_replacement_rejects_stale_expected_hash_and_leaves_no_temp(project):
    store, _, _, _ = project
    target = store.root / "note.md"
    target.write_text("user content")
    with pytest.raises(ScsError):
        store.paths.replace_checked("note.md", b"overwritten", "0" * 64)
    assert target.read_text() == "user content"
    assert not list(store.root.glob(".scs-write-*"))


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_mcp_view_tools_auto_refresh_and_conflict_does_not_undo_work(tmp_path, mode):
    from mcp import Client
    from scs_mcp.server import create_server
    async with Client(create_server(tmp_path), mode=mode) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content
        sid = (await call("session_open", project_root=str(tmp_path), client_name="mcp-test", explicit_skip=True))["id"]
        initial = await call("project_view_info", session_id=sid)
        assert not initial["view_initialized"] and not (tmp_path / "docs").exists()
        out = await call("project_view_refresh", session_id=sid, initialize_vault=True)
        assert out["auto_refresh_enabled"]
        p = await call("work_package_create", session_id=sid, goal="新的工作", allowed_files=["src"], acceptance=["记录完整"],
                       subtasks=[{"id": "A", "kind": "analyze", "allowed_files": ["src"], "acceptance": ["报告"]}], request_key="auto")
        assert p["status"] == "OK" and p["view_update"]["status"] == "OK"
        info = await call("project_view_info", session_id=sid)
        assert not info["needs_refresh"]
        page = tmp_path / info["overview_path"]
        page.write_text(page.read_text().replace("项目资料与工作记录", "手工更改生成区域"))
        result = await call("work_package_update", session_id=sid, package_id=p["id"], expected_version=1, state="IN_PROGRESS")
        assert result["status"] == "OK" and result["version"] == 2
        assert result["view_update"]["error_code"] == "VIEW_CONFLICT" and result["view_update"]["work_package_saved"]
        assert (await call("work_package_get", session_id=sid, package_id=p["id"]))["state"] == "IN_PROGRESS"
        assert "手工更改生成区域" in page.read_text()


async def test_disable_auto_refresh_and_bad_session(tmp_path):
    from mcp import Client
    from scs_mcp.server import create_server
    async with Client(create_server(tmp_path)) as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content
        for tool in ("project_view_info", "project_view_refresh"):
            assert (await call(tool, session_id="missing"))["error_code"] == "SESSION_NOT_FOUND"
        sid = (await call("session_open", project_root=str(tmp_path), client_name="mcp-test", explicit_skip=True))["id"]
        out = await call("project_view_refresh", session_id=sid, auto_refresh=False)
        assert not out["auto_refresh_enabled"]
        p = await call("work_package_create", session_id=sid, goal="g", allowed_files=["src"], acceptance=["a"],
            subtasks=[{"id": "A", "kind": "analyze", "allowed_files": ["src"], "acceptance": ["a"]}], request_key="auto")
        assert "view_update" not in p
        assert (await call("project_view_info", session_id=sid))["snapshot_stale"]


def test_view_refresh_waits_for_intake_and_investigate_stays_read_only(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "host", "c")["id"]
    view = ProjectView(store)
    with pytest.raises(ScsError) as err:
        view.refresh(sid, True)
    assert err.value.code == "INTAKE_REQUIRED"
    q = state.request(sid, "mode", ["investigate"], "text")
    state.submit(sid, q["id"], "accept", ["investigate"])
    assert not view.info(sid)["view_initialized"]
    with pytest.raises(ScsError) as err:
        view.refresh(sid, True)
    assert err.value.code == "READ_ONLY_MODE"
    assert not (tmp_path / "docs").exists()


def test_closed_package_moves_to_closed_view(project):
    store, sid, work, view = project
    p = package(project)
    closed = work.update(sid, p["id"], p["version"], "ABANDONED", close_reason="superseded by WP-108")
    out = view.refresh(sid, True)
    closed_page = store.root / out["docs_root"] / "agent/acceptance/40_已关闭.md"
    progress_page = store.root / out["docs_root"] / "agent/acceptance/20_进行中.md"
    assert closed_page.is_file()
    assert p["id"] in closed_page.read_text(encoding="utf-8")
    assert p["id"] not in progress_page.read_text(encoding="utf-8")
    package_page = store.root / out["docs_root"] / f"agent/acceptance/work-packages/{p['id']}.md"
    assert "关闭原因：" in package_page.read_text(encoding="utf-8")
    assert closed["state"] == "ABANDONED"
