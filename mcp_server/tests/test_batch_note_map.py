"""Atomic attempt batches, attempt notes, and the read-only run map."""

import pytest

from scs_mcp.errors import ScsError
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", "c", explicit_skip=True)["id"]
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    return store, state, sid, Work(store)


def make(work, sid, key="batch", subtasks=None):
    subtasks = subtasks or [
        {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]},
        {"id": "B", "kind": "test", "allowed_files": ["src/a.py"], "acceptance": ["b"]},
        {"id": "C", "kind": "implement", "allowed_files": ["src/a.py"], "acceptance": ["c"]},
    ]
    package = work.create(sid, "batch package", ["src"], ["done"], subtasks, key)
    return work.update(sid, package["id"], package["version"], "IN_PROGRESS")


def test_prepare_many_is_atomic_and_does_not_change_version(env):
    _, _, sid, work = env
    package = make(work, sid)
    prepared = work.prepare_attempts(sid, package["id"], package["version"], ["A", "B"])
    assert prepared["version"] == package["version"]
    assert [entry["subtask_id"] for entry in prepared["attempts"]] == ["A", "B"]
    assert all(entry["attempt"]["state"] == "PREPARED" for entry in prepared["attempts"])
    assert all(entry["warnings"] == [] for entry in prepared["attempts"])
    assert work.get(sid, package["id"])["version"] == package["version"]
    assert len(work.list_attempts(sid, package["id"])["attempts"]) == 2


def test_prepare_many_rejects_bad_inputs_without_side_effects(env):
    store, _, sid, work = env
    package = make(work, sid)
    with pytest.raises(ScsError) as err:
        work.prepare_attempts(sid, package["id"], package["version"], [])
    assert err.value.code == "INPUT_INVALID"
    with pytest.raises(ScsError) as err:
        work.prepare_attempts(sid, package["id"], package["version"], ["A", "A"])
    assert err.value.code == "INPUT_INVALID"
    with pytest.raises(ScsError) as err:
        work.prepare_attempts(sid, package["id"], package["version"], ["A"],
                              host_agent_ids=["x", "y"])
    assert err.value.code == "INPUT_INVALID"
    with pytest.raises(ScsError) as err:
        work.prepare_attempts(sid, package["id"], package["version"] + 1, ["A"])
    assert err.value.code == "VERSION_CONFLICT"
    with pytest.raises(ScsError) as err:
        work.prepare_attempts(sid, package["id"], package["version"], ["A", "missing"])
    assert err.value.code == "SUBTASK_INVALID"
    with store.transaction() as con:
        assert con.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM leases").fetchone()[0] == 0


def test_prepare_many_rolls_back_when_a_later_item_fails(env):
    store, _, sid, work = env
    package = make(work, sid, "rollback", [
        {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]},
        {"id": "B", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["b"],
         "depends_on": ["A"]},
    ])
    with pytest.raises(ScsError) as err:
        work.prepare_attempts(sid, package["id"], package["version"], ["A", "B"])
    assert err.value.code == "DEPENDENCY_NOT_READY"
    with store.transaction() as con:
        assert con.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM leases").fetchone()[0] == 0
    assert work.get(sid, package["id"])["version"] == package["version"]


def test_update_many_increments_version_once_and_replays(env):
    store, _, sid, work = env
    package = make(work, sid)
    prepared = work.prepare_attempts(sid, package["id"], package["version"], ["A", "B"])
    ids = [entry["attempt"]["id"] for entry in prepared["attempts"]]
    updates = [{"attempt_id": ids[0], "result": {"status": "NOT_RUN"}},
               {"attempt_id": ids[1], "result": {"status": "NOT_RUN"}}]
    out = work.update_attempts(sid, updates, "batch-op")
    assert out["version"] == package["version"] + 1
    assert out["task_statuses"] == {"A": "NOT_RUN", "B": "NOT_RUN", "C": "NOT_RUN"}
    assert [attempt["state"] for attempt in out["attempts"]] == ["ENDED", "ENDED"]
    assert work.get(sid, package["id"])["version"] == out["version"]

    # Replay from a fresh Store: the receipt is durable and checked before ENDED.
    replay = Work(Store(store.root)).update_attempts(sid, updates, "batch-op")
    assert replay == out
    assert work.get(sid, package["id"])["version"] == out["version"]

    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, [{"attempt_id": ids[0], "result": {"status": "FAIL"}}],
                             "batch-op")
    assert err.value.code == "OPERATION_CONFLICT"
    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, updates, "another-op")
    assert err.value.code == "ATTEMPT_CLOSED"


def test_update_many_rolls_back_the_whole_batch_on_one_failure(env):
    store, _, sid, work = env
    package = make(work, sid)
    prepared = work.prepare_attempts(sid, package["id"], package["version"], ["A", "B"])
    ids = [entry["attempt"]["id"] for entry in prepared["attempts"]]
    updates = [{"attempt_id": ids[0], "result": {"status": "NOT_RUN"}},
               {"attempt_id": ids[1], "result": {"status": "PASS"}}]  # no evidence
    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, updates, "rollback-op")
    assert err.value.code == "EVIDENCE_REQUIRED"
    assert work.get(sid, package["id"])["version"] == package["version"]
    states = [attempt["state"] for attempt in work.list_attempts(sid, package["id"])["attempts"]]
    assert states == ["PREPARED", "PREPARED"]
    with store.transaction() as con:
        assert con.execute(
            "SELECT count(*) FROM work_operations WHERE id='rollback-op'").fetchone()[0] == 0


def test_update_many_rejects_cross_package_and_bad_inputs(env):
    _, _, sid, work = env
    first = make(work, sid, "one")
    second = make(work, sid, "two")
    a = work.prepare_attempt(sid, first["id"], first["version"], "A")
    b = work.prepare_attempt(sid, second["id"], second["version"], "A")
    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, [
            {"attempt_id": a["attempt"]["id"], "result": {"status": "NOT_RUN"}},
            {"attempt_id": b["attempt"]["id"], "result": {"status": "NOT_RUN"}},
        ], "cross-package")
    assert err.value.code == "BATCH_PACKAGE_MISMATCH"
    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, [], "empty")
    assert err.value.code == "INPUT_INVALID"
    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, [{"attempt_id": a["attempt"]["id"],
                                    "result": {"status": "NOT_RUN"}, "extra": True}], "bad")
    assert err.value.code == "INPUT_INVALID"
    with pytest.raises(ScsError) as err:
        work.update_attempts(sid, [{"attempt_id": a["attempt"]["id"],
                                    "result": {"status": "NOT_RUN"}}], "bad id!")
    assert err.value.code == "INPUT_INVALID"


def test_attempt_ended_replay_survives_a_new_store(env):
    store, _, sid, work = env
    package = make(work, sid)
    attempt = work.prepare_attempt(sid, package["id"], package["version"], "A")
    args = {"state": "ENDED", "result": {"status": "NOT_RUN"}, "operation_id": "attempt-op"}
    first = work.update_attempt(sid, attempt["attempt"]["id"], package["version"], **args)
    replay = Work(Store(store.root)).update_attempt(
        sid, attempt["attempt"]["id"], package["version"], **args)
    assert replay == first
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt["attempt"]["id"], first["version"], "ENDED",
                            {"status": "NOT_RUN"})
    assert err.value.code == "ATTEMPT_CLOSED"


def test_note_is_idempotent_without_version_or_lease_change(env):
    store, _, sid, work = env
    package = make(work, sid)
    attempt = work.prepare_attempt(sid, package["id"], package["version"], "C")
    attempt_id = attempt["attempt"]["id"]
    assert attempt["leases"]
    with store.transaction() as con:
        expires_before = con.execute(
            "SELECT expires FROM leases WHERE attempt_id=?", (attempt_id,)).fetchone()[0]
    first = work.note(sid, attempt_id, "compile still running", "note-1")
    assert first["status"] == "OK" and first["recorded"] is True
    assert first["liveness_proven"] is False and first["lease_renewed"] is False
    assert first["version_changed"] is False
    assert work.get(sid, package["id"])["version"] == package["version"]
    with store.transaction() as con:
        assert con.execute("SELECT expires FROM leases WHERE attempt_id=?",
                           (attempt_id,)).fetchone()[0] == expires_before
        assert con.execute("SELECT count(*) FROM events WHERE tool='work_attempt_note'"
                           ).fetchone()[0] == 1
    replay = work.note(sid, attempt_id, "compile still running", "note-1")
    assert replay == first
    with pytest.raises(ScsError) as err:
        work.note(sid, attempt_id, "different text", "note-1")
    assert err.value.code == "OPERATION_CONFLICT"
    second = work.note(sid, attempt_id, "compile done", "note-2")
    assert second["recorded"] is True and second["note_id"] == "note-2"


def test_note_bounds_and_missing_attempt(env):
    _, _, sid, work = env
    package = make(work, sid)
    attempt = work.prepare_attempt(sid, package["id"], package["version"], "A")
    attempt_id = attempt["attempt"]["id"]
    with pytest.raises(ScsError) as err:
        work.note(sid, attempt_id, "x" * 2001, "too-long")
    assert err.value.code == "INPUT_INVALID"
    with pytest.raises(ScsError) as err:
        work.note(sid, attempt_id, "   ", "blank")
    assert err.value.code == "INPUT_INVALID"
    with pytest.raises(ScsError) as err:
        work.note(sid, attempt_id, "ok", "bad id!")
    assert err.value.code == "INPUT_INVALID"
    with pytest.raises(ScsError) as err:
        work.note(sid, "A-missing", "ok", "missing")
    assert err.value.code == "ATTEMPT_NOT_FOUND"


def test_run_map_is_read_only_bounded_and_escapes_mermaid(env):
    store, _, sid, work = env
    package = work.create(sid, 'weird "goal" [x] #1 <tag>', ["src"], ["done"],
        [{"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]}],
        "map")
    package = work.update(sid, package["id"], package["version"], "IN_PROGRESS")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], "A")
    attempt_id = attempt["attempt"]["id"]
    for index in range(4):
        work.note(sid, attempt_id, f"note {index}", f"n{index}")
    with store.transaction() as con:
        events_before = con.execute("SELECT count(*) FROM events").fetchone()[0]

    out = work.run_map(sid)
    assert out["status"] == "OK" and out["truncated"] is False
    assert out["packages"][0]["id"] == package["id"]
    assert out["packages"][0]["review_required"] is False
    assert out["limits"] == {"packages": 64, "attempts": 200, "notes_per_attempt": 3}
    assert len(out["notes"][attempt_id]) == 3
    assert "graph TD" in out["mermaid"]
    assert "#quot;goal#quot;" in out["mermaid"]
    assert "#91;x#93;" in out["mermaid"]
    assert "#35;1" in out["mermaid"]
    assert "#60;tag#62;" in out["mermaid"]
    assert "ACTIVE_ATTEMPTS" in [blocker["code"] for blocker in out["blockers"]]

    with store.transaction() as con:
        assert con.execute("SELECT count(*) FROM events").fetchone()[0] == events_before
    assert work.get(sid, package["id"])["version"] == package["version"]


def test_run_map_limit_revision_and_access_scoping(env):
    store, state, sid, work = env
    first = make(work, sid, "map-one")
    second = make(work, sid, "map-two")
    out = work.run_map(sid, limit=1)
    assert out["truncated"] is True and len(out["packages"]) == 1
    filtered = work.run_map(sid, package_id=first["id"], revision=1)
    assert [entry["id"] for entry in filtered["packages"]] == [first["id"]]
    assert filtered["truncated"] is False
    with pytest.raises(ScsError) as err:
        work.run_map(sid, package_id=first["id"], revision=9)
    assert err.value.code == "REVISION_NOT_FOUND"
    with pytest.raises(ScsError) as err:
        work.run_map(sid, revision=1)
    assert err.value.code == "INPUT_INVALID"
    for limit in (0, True):
        with pytest.raises(ScsError) as err:
            work.run_map(sid, limit=limit)
        assert err.value.code == "INPUT_INVALID"
    other = state.open(str(store.root), "test", "other")["id"]
    with pytest.raises(ScsError) as err:
        work.run_map(other, package_id=second["id"])
    assert err.value.code == "HANDOFF_REQUIRED"
