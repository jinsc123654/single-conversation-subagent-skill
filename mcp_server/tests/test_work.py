import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from scs_mcp.errors import ScsError
from scs_mcp.locks import Leases
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", "c", explicit_skip=True)["id"]
    return store, state, sid, Work(store)


def package(work, sid, key="one", kind="test"):
    return work.create(sid, "Bounded implementation", ["src"], ["preserve behavior"],
        [{"id": "T1", "kind": kind, "allowed_files": ["src/a.py"], "acceptance": ["evidence"]}], key)


def evidence_file(setup, p, value):
    store, _, _, _ = setup
    path = f"{p['docs_root']}/agent/evidence/{p['id']}/R{p['revision']}/T1/result.json"
    (store.root / path).write_text(json.dumps(value), encoding="utf-8")
    return path


def test_create_idempotence_preserves_manual_records_and_docs_priority(setup):
    store, _, sid, work = setup
    (store.root / "00_doc/agent").mkdir(parents=True)
    (store.root / "00_doc/agent/AGENT_PROJECT.md").write_text("USER EDIT\n")
    (store.root / "docs").mkdir()
    p = package(work, sid)
    assert p["docs_root"] == "00_doc"
    assert (store.root / "00_doc/agent/AGENT_PROJECT.md").read_text() == "USER EDIT\n"
    assert package(work, sid)["id"] == p["id"]
    with pytest.raises(ScsError):
        work.create(sid, "changed", ["src"], ["x"], [{"id": "T1", "kind": "test", "allowed_files": ["src/a.py"], "acceptance": ["x"]}], "one")
    assert p["record_sync_required"] and not p["final_acceptance"]


@pytest.mark.parametrize("docs", ["00_doc", "00_docs", "docs", "doc", None])
def test_existing_docs_layouts(setup, docs):
    store, _, sid, work = setup
    if docs:
        (store.root / docs).mkdir()
    p = package(work, sid)
    assert p["docs_root"] == (docs or "docs")


@pytest.mark.parametrize("value,expected", [
    ({"exit_code": 1, "passed": 1, "command": "pytest"}, "INVALID"),
    ({"exit_code": 0, "passed": 0, "command": "pytest"}, "INSUFFICIENT"),
    ({"passed": 5}, "INVALID"),
    ({"exit_code": False, "passed": 5, "command": "pytest"}, "INVALID"),
    ({"exit_code": 0, "passed": True, "command": "pytest"}, "INSUFFICIENT"),
    ({"exit_code": 0, "passed": 5, "command": "pytest -q"}, "VERIFIED"),
])
def test_test_evidence_return_code_and_required_content(setup, value, expected):
    _, _, sid, work = setup
    p = package(work, sid)
    path = evidence_file(setup, p, value)
    check = work.evidence(sid, p["id"], 1, "T1", path)
    assert check["status"] == expected
    assert work.evidence(sid, p["id"], 1, "T1", path, ["absent marker"])["status"] == "INSUFFICIENT"


def test_missing_empty_traversal_and_symlink_never_verify(setup, tmp_path):
    store, _, sid, work = setup
    p = package(work, sid)
    path = evidence_file(setup, p, {})
    full = store.root / path
    full.write_text("")
    assert work.evidence(sid, p["id"], 1, "T1", path)["status"] == "INSUFFICIENT"
    full.unlink()
    assert work.evidence(sid, p["id"], 1, "T1", path)["status"] == "INVALID"
    assert work.evidence(sid, p["id"], 1, "T1", "../outside")["status"] == "INVALID"
    full.symlink_to("/etc/passwd")
    assert work.evidence(sid, p["id"], 1, "T1", path)["status"] == "INVALID"
    assert work.evidence(sid, p["id"], 1, "T1", "/etc/passwd")["status"] == "INVALID"


def test_pass_needs_unchanged_evidence_and_optimistic_version(setup):
    store, _, sid, work = setup
    p = package(work, sid)
    with pytest.raises(ScsError):
        work.update(sid, p["id"], 1, results=[{"id": "T1", "status": "PASS"}])
    path = evidence_file(setup, p, {"exit_code": 0, "passed": 1, "command": "pytest"})
    check = work.evidence(sid, p["id"], 1, "T1", path)
    result = {"id": "T1", "status": "PASS", "evidence": [{"path": path, "sha256": check["sha256"]}]}
    (store.root / path).write_text("changed")
    with pytest.raises(ScsError):
        work.update(sid, p["id"], 1, results=[result])
    path = evidence_file(setup, p, {"exit_code": 0, "passed": 1, "command": "pytest"})
    p = work.update(sid, p["id"], 1, "IN_PROGRESS", [result])
    assert p["version"] == 2 and p["subtasks"][0]["status"] == "PASS"
    with pytest.raises(ScsError):
        work.update(sid, p["id"], 1, "BLOCKED")
    with pytest.raises(ScsError):
        work.update(sid, p["id"], 2, "ACCEPTED")
    p = work.update(sid, p["id"], 2, "REVIEW_READY")
    p = work.update(sid, p["id"], 3, new_revision=True)
    assert p["revision"] == 2 and p["subtasks"][0]["status"] == "NOT_RUN"
    assert (store.root / path).exists()
    with pytest.raises(ScsError):
        work.evidence(sid, p["id"], 1, "T1", path)


def test_handoff_does_not_copy_preferences_and_rejects_stale(setup):
    store, state, sid, work = setup
    p = package(work, sid)
    export = work.export(sid, p["id"], 1)
    other = state.open(str(store.root), "other", "new")["id"]
    with pytest.raises(ScsError):
        work.get(other, p["id"])
    with pytest.raises(ScsError):
        work.resume(other, export["handoff_id"], "bad-digest")
    assert work.resume(other, export["handoff_id"], export["sha256"])["session_choices_copied"] is False
    assert work.get(other, p["id"])["id"] == p["id"]
    assert state.get(other)["mode"] is None
    work.update(sid, p["id"], 1, "IN_PROGRESS")
    with pytest.raises(ScsError):
        work.resume(other, export["handoff_id"], export["sha256"])


def test_leases_contention_expiry_and_stale_tokens(setup):
    store, state, sid, _ = setup
    other = state.open(str(store.root), "other", "c")["id"]
    clock = [1000.0]
    leases = Leases(store, lambda: clock[0])
    a = leases.acquire(sid, "serial:board-A", 2)
    assert leases.acquire(other, "serial:board-A")["status"] == "CONFLICT"
    assert leases.acquire(sid, "serial:board-A")["status"] == "CONFLICT"
    assert leases.acquire(sid, "serial:board-A", 2, a["lease_token"])["lease_token"] == a["lease_token"]
    clock[0] += 3
    b = leases.acquire(other, "serial:board-A")
    with pytest.raises(ScsError):
        leases.release(sid, "serial:board-A", a["lease_token"])
    assert leases.release(other, "serial:board-A", b["lease_token"])["status"] == "RELEASED"
    assert leases.release(other, "serial:board-A", b["lease_token"])["status"] == "ALREADY_RELEASED"
    assert not a["external_processes_excluded"]


def test_concurrent_creation_and_same_resource_competition(setup):
    store, state, sid, work = setup
    def create(i):
        return package(Work(Store(store.root)), sid, "package-" + str(i))["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(8)))
    assert len(set(ids)) == 8
    sessions = [state.open(str(store.root), "test", str(i))["id"] for i in range(4)]
    def acquire(owner):
        return Leases(Store(store.root)).acquire(owner, "file:src/a.py")["status"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(acquire, sessions))
    assert results.count("ACQUIRED") == 1 and results.count("CONFLICT") == 3


def test_database_lock_failure_then_restart_recovery(setup):
    store, state, sid, _ = setup
    fast = Store(store.root, busy_timeout_ms=20)
    con = sqlite3.connect(store.db)
    con.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ScsError) as error:
            State(fast).request(sid, "mode", ["quick"], "text")
        assert error.value.code == "DB_BUSY"
    finally:
        con.rollback()
        con.close()
    restarted = State(Store(store.root))
    assert restarted.get(sid)["mode"] == "quick"


def test_no_docs_created_for_small_question(tmp_path):
    state = State(Store(tmp_path))
    sid = state.open(str(tmp_path), "host", "c")["id"]
    q = state.request(sid, "mode", ["quick"], "text")
    state.submit(sid, q["id"], "accept", ["quick"])
    assert not (tmp_path / "docs").exists()


def test_scope_cannot_expand_and_bad_docs_symlink(setup):
    store, _, sid, work = setup
    with pytest.raises(ScsError):
        work.create(sid, "g", ["src"], ["a"], [{"id": "t", "kind": "test", "allowed_files": ["other"], "acceptance": ["a"]}], "x")
    (store.root / "00_doc").symlink_to("/tmp")
    with pytest.raises(ScsError):
        package(work, sid)


def test_changed_pass_evidence_blocks_export_and_state_transition(setup):
    store, _, sid, work = setup
    p = package(work, sid)
    path = evidence_file(setup, p, {"exit_code": 0, "passed": 1, "command": "pytest"})
    check = work.evidence(sid, p["id"], 1, "T1", path)
    p = work.update(sid, p["id"], 1, "IN_PROGRESS", [{"id": "T1", "status": "PASS", "evidence": [{"path": path, "sha256": check["sha256"]}]}])
    (store.root / path).write_text("tampered")
    with pytest.raises(ScsError):
        work.export(sid, p["id"], p["version"])
    with pytest.raises(ScsError):
        work.update(sid, p["id"], p["version"], "REVIEW_READY")
    # An explicit correction can withdraw the report; it does not delete evidence.
    p = work.update(sid, p["id"], p["version"], results=[{"id": "T1", "status": "NOT_RUN"}])
    assert p["subtasks"][0]["status"] == "NOT_RUN"


def test_schema_rejects_newer_and_state_symlinks(tmp_path):
    store = Store(tmp_path)
    with sqlite3.connect(store.db) as con:
        con.execute("PRAGMA user_version=999")
    with pytest.raises(ScsError) as error:
        Store(tmp_path)
    assert error.value.code == "SCHEMA_UNSUPPORTED"
    other = tmp_path / "other"
    other.mkdir()
    (other / ".scs").symlink_to(tmp_path / ".scs")
    with pytest.raises(ScsError):
        Store(other)


def test_independent_processes_create_unique_packages(setup):
    import os
    import subprocess
    import sys
    from pathlib import Path
    store, _, sid, _ = setup
    script = '''
import sys
from scs_mcp.storage import Store
from scs_mcp.work import Work
work=Work(Store(sys.argv[1]))
p=work.create(sys.argv[2], "bounded", ["src"], ["done"], [{"id":"T1","kind":"analyze","allowed_files":["src"],"acceptance":["report"]}], sys.argv[3])
print(p["id"])
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    procs = [subprocess.Popen([sys.executable, "-c", script, str(store.root), sid, "proc-" + str(i)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
    ids = []
    try:
        for proc in procs:
            out, err = proc.communicate(timeout=10)
            assert proc.returncode == 0, err
            ids.append(out.strip())
        assert len(set(ids)) == 4
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


@pytest.mark.parametrize("selection", ["none", "method-only"])
def test_no_testing_scope_cannot_be_reported_pass(setup, selection):
    store, state, sid, work = setup
    q = state.request(sid, "verification", [selection], "text")  # quick skips the followup
    assert q["status"] == "SKIPPED"
    # A fresh standard conversation confirms its own no-testing scope.
    sid = state.open(str(store.root), "test", "standard-chat")["id"]
    q = state.request(sid, "mode", ["standard"], "text")
    state.submit(sid, q["id"], "accept", ["standard"])
    q = state.request(sid, "verification", [selection], "text")
    state.submit(sid, q["id"], "accept", [selection])
    p = package(work, sid)
    path = evidence_file(setup, p, {"exit_code": 0, "passed": 1, "command": "pytest"})
    check = work.evidence(sid, p["id"], 1, "T1", path)
    with pytest.raises(ScsError) as err:
        work.update(sid, p["id"], 1, results=[{"id": "T1", "status": "PASS", "evidence": [{"path": path, "sha256": check["sha256"]}]}])
    assert err.value.code == "VALIDATION_NOT_AUTHORIZED"
    assert work.update(sid, p["id"], 1, results=[{"id": "T1", "status": "NOT_RUN"}])["subtasks"][0]["status"] == "NOT_RUN"


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_sqlite_companion_symlink_is_rejected(tmp_path, suffix):
    store = Store(tmp_path)
    target = tmp_path / "innocent.txt"
    target.write_text("keep")
    path = tmp_path / (".scs/state.sqlite3" + suffix)
    if path.exists():
        path.unlink()
    path.symlink_to(target)
    with pytest.raises(ScsError) as err:
        Store(tmp_path)
    assert err.value.code == "PATH_UNSAFE"
    assert target.read_text() == "keep"
