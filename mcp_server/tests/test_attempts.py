import hashlib
import json
import sqlite3

import pytest

from scs_mcp.errors import ScsError
from scs_mcp.obsidian import ProjectView
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", "c", explicit_skip=True)["id"]
    return store, state, sid, Work(store)


def make(work, sid, subtasks, key="one", allowed=("src", "tests")):
    return work.create(sid, "attempt test", list(allowed), ["done"], subtasks, key)


def write_evidence(root, package, subtask, attempt_id, value="done\n"):
    path = f"{package['docs_root']}/agent/evidence/{package['id']}/R{package['revision']}/{subtask}/{attempt_id}/report.txt"
    (root / path).write_text(value, encoding="utf-8")
    return path, hashlib.sha256((root / path).read_bytes()).hexdigest()


def test_dependency_lifecycle_and_quiescence(setup):
    store, _, sid, work = setup
    p = make(work, sid, [
        {"id": "A1", "kind": "implement", "allowed_files": ["src/a.py"], "acceptance": ["implemented"]},
        {"id": "A2", "kind": "test", "allowed_files": ["tests"], "acceptance": ["tested"], "depends_on": ["A1"]},
    ])
    p = work.update(sid, p["id"], p["version"], "IN_PROGRESS")
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, p["id"], p["version"], "A2")
    assert err.value.code == "DEPENDENCY_NOT_READY"

    a1 = work.prepare_attempt(sid, p["id"], p["version"], "A1")
    path, sha = write_evidence(store.root, p, "A1", a1["attempt"]["id"])
    ended = work.update_attempt(sid, a1["attempt"]["id"], p["version"], "ENDED",
        {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]})
    assert ended["version"] == p["version"] + 1
    assert ended["task_status"] == "PASS"

    a2 = work.prepare_attempt(sid, p["id"], ended["version"], "A2")
    with pytest.raises(ScsError) as err:
        work.update(sid, p["id"], ended["version"], state="REVIEW_READY")
    assert err.value.code == "ATTEMPTS_ACTIVE"

    test_path, test_sha = write_evidence(store.root, p, "A2", a2["attempt"]["id"],
        json.dumps({"command": "pytest", "exit_code": 0, "passed": 1}))
    ended2 = work.update_attempt(sid, a2["attempt"]["id"], ended["version"], "ENDED",
        {"status": "PASS", "evidence": [{"path": test_path, "sha256": test_sha}]})
    review = work.update(sid, p["id"], ended2["version"], state="REVIEW_READY")
    assert review["state"] == "REVIEW_READY"
    assert {task["id"]: task["status"] for task in review["subtasks"]} == {"A1": "PASS", "A2": "PASS"}
    assert [attempt["state"] for attempt in work.get(sid, p["id"])["attempts"]] == ["ENDED", "ENDED"]
    with store.transaction() as con:
        assert con.execute("SELECT count(*) FROM leases").fetchone()[0] == 0


def test_atomic_resource_set_and_read_parallel(setup):
    store, _, sid, work = setup
    p = make(work, sid, [
        {"id": "B1", "kind": "implement", "allowed_files": ["src/a.py"], "acceptance": ["a"],
         "resources": [{"resource": "file:src/a.py", "mode": "write"},
                       {"resource": "device:board", "mode": "write"}]},
        {"id": "B2", "kind": "implement", "allowed_files": ["src/b.py"], "acceptance": ["b"],
         "resources": [{"resource": "file:src/b.py", "mode": "write"},
                       {"resource": "device:board", "mode": "write"}]},
        {"id": "R1", "kind": "analyze", "allowed_files": ["src/read.txt"], "acceptance": ["r1"]},
        {"id": "R2", "kind": "analyze", "allowed_files": ["src/read.txt"], "acceptance": ["r2"]},
    ])
    p = work.update(sid, p["id"], p["version"], "IN_PROGRESS")
    work.prepare_attempt(sid, p["id"], p["version"], "B1")
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, p["id"], p["version"], "B2")
    assert err.value.code == "RESOURCE_BUSY"
    with store.transaction() as con:
        resources = {row[0] for row in con.execute("SELECT resource FROM leases")}
    assert "file:src/b.py" not in resources
    first = work.prepare_attempt(sid, p["id"], p["version"], "R1")
    second = work.prepare_attempt(sid, p["id"], p["version"], "R2")
    assert first["attempt"]["state"] == "PREPARED" and second["attempt"]["state"] == "PREPARED"


def test_unknown_attempt_keeps_expired_lease_busy(setup):
    store, _, sid, work = setup
    p = make(work, sid, [
        {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"],
         "resources": [{"resource": "device:board", "mode": "write"}]},
        {"id": "B", "kind": "analyze", "allowed_files": ["src/b.py"], "acceptance": ["b"],
         "resources": [{"resource": "device:board", "mode": "write"}]},
    ])
    p = work.update(sid, p["id"], p["version"], "IN_PROGRESS")
    a = work.prepare_attempt(sid, p["id"], p["version"], "A")
    work.update_attempt(sid, a["attempt"]["id"], p["version"], "UNKNOWN")
    with store.transaction(True) as con:
        con.execute("UPDATE leases SET expires=0")
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, p["id"], p["version"], "B")
    assert err.value.code == "RESOURCE_BUSY"
    ended = work.update_attempt(sid, a["attempt"]["id"], p["version"], "ENDED", {"status": "NOT_RUN"})
    assert ended["task_status"] == "NOT_RUN"
    b = work.prepare_attempt(sid, p["id"], ended["version"], "B")
    assert b["attempt"]["state"] == "PREPARED"


def test_operation_idempotency_for_package_and_attempt(setup):
    store, _, sid, work = setup
    p = make(work, sid, [{"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]}])
    first = work.update(sid, p["id"], p["version"], "IN_PROGRESS", operation_id="package-op")
    replay = work.update(sid, p["id"], p["version"], "IN_PROGRESS", operation_id="package-op")
    assert replay == first
    with pytest.raises(ScsError) as err:
        work.update(sid, p["id"], first["version"], "IN_PROGRESS", operation_id="package-op")
    assert err.value.code == "OPERATION_CONFLICT"

    attempt = work.prepare_attempt(sid, p["id"], first["version"], "A")
    path, sha = write_evidence(store.root, p, "A", attempt["attempt"]["id"])
    ended = work.update_attempt(sid, attempt["attempt"]["id"], first["version"], "ENDED",
        {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]}, operation_id="attempt-op")
    replay = work.update_attempt(sid, attempt["attempt"]["id"], first["version"], "ENDED",
        {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]}, operation_id="attempt-op")
    assert replay == ended


def test_new_revision_definition_waits_for_quiescence(setup):
    _, _, sid, work = setup
    p = make(work, sid, [{"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]}])
    p = work.update(sid, p["id"], p["version"], "IN_PROGRESS")
    attempt = work.prepare_attempt(sid, p["id"], p["version"], "A")
    definition = {"goal": "replanned", "allowed_files": ["src"], "acceptance": ["new"],
                  "subtasks": [{"id": "B", "kind": "implement", "allowed_files": ["src/b.py"],
                                "acceptance": ["b"], "depends_on": []}]}
    with pytest.raises(ScsError) as err:
        work.update(sid, p["id"], p["version"], new_revision=True, definition=definition)
    assert err.value.code == "ATTEMPTS_ACTIVE"
    ended = work.update_attempt(sid, attempt["attempt"]["id"], p["version"], "ENDED", {"status": "NOT_RUN"})
    revised = work.update(sid, p["id"], ended["version"], new_revision=True, definition=definition)
    assert revised["revision"] == 2 and revised["state"] == "PENDING"
    assert [task["id"] for task in revised["subtasks"]] == ["B"]


def test_dependency_cycle_and_unknown_dependency_rejected(setup):
    _, _, sid, work = setup
    with pytest.raises(ScsError) as err:
        make(work, sid, [
            {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"], "depends_on": ["B"]},
            {"id": "B", "kind": "analyze", "allowed_files": ["src/b.py"], "acceptance": ["b"], "depends_on": ["A"]},
        ])
    assert err.value.code == "DEPENDENCY_CYCLE"
    with pytest.raises(ScsError) as err:
        make(work, sid, [{"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"],
                          "acceptance": ["a"], "depends_on": ["missing"]}], key="two")
    assert err.value.code == "SUBTASK_INVALID"


def test_view_lists_attempts(setup):
    store, _, sid, work = setup
    p = make(work, sid, [{"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]}])
    p = work.update(sid, p["id"], p["version"], "IN_PROGRESS")
    attempt = work.prepare_attempt(sid, p["id"], p["version"], "A")
    out = ProjectView(store).refresh(sid, True)
    attempts_page = store.root / out["docs_root"] / "agent/acceptance/60_执行实例.md"
    package_page = store.root / out["docs_root"] / f"agent/acceptance/work-packages/{p['id']}.md"
    assert attempt["attempt"]["id"] in attempts_page.read_text(encoding="utf-8")
    assert "执行实例" in package_page.read_text(encoding="utf-8")
    assert out["attempts"] == 1


def test_v2_leases_table_migrates_to_v3(tmp_path):
    root = tmp_path
    (root / ".scs").mkdir()
    db = root / ".scs/state.sqlite3"
    with sqlite3.connect(db) as con:
        con.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sessions(id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, client TEXT NOT NULL,
          temporary INTEGER NOT NULL, review_registration TEXT NOT NULL, mode TEXT,
          verification TEXT, codex_review INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE work_packages(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, revision INTEGER NOT NULL,
          version INTEGER NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, docs_root TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE work_package_revisions(package_id TEXT NOT NULL, revision INTEGER NOT NULL,
          version INTEGER NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, docs_root TEXT NOT NULL,
          captured_at TEXT NOT NULL, PRIMARY KEY(package_id, revision));
        CREATE TABLE leases(resource TEXT PRIMARY KEY, owner TEXT NOT NULL,
          token TEXT UNIQUE NOT NULL, expires REAL NOT NULL);
        PRAGMA user_version=2;
        """)
        con.execute("INSERT INTO metadata VALUES('project_root', ?)", (str(root),))
    store = Store(root)
    with store.transaction() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
        columns = {row[1] for row in con.execute("PRAGMA table_info(leases)")}
        assert {"mode", "attempt_id", "package_id"} <= columns
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"work_attempts", "work_operations"} <= tables
