"""codex_gate question, shared intake, and legacy conversation compatibility."""

import json

import pytest

from scs_mcp import state as state_module
from scs_mcp.errors import ScsError
from scs_mcp.state import State
from scs_mcp.storage import Store, encode
from scs_mcp.work import Work


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    return store, state, Work(store)


def open_chat(state, chat="chat", registration="unknown", skip=False):
    return state.open(str(state.store.root), "test-host", chat,
                      review_registration=registration, explicit_skip=skip)["id"]


def choose(state, sid, kind, choices):
    q = state.request(sid, kind, choices, "text")
    assert q.get("kind") == kind, q
    result = state.submit(sid, q["id"], "accept", choices)
    assert result["status"] == "CONFIRMED"
    return q


def open_long(state, chat="long", registration="unknown"):
    sid = open_chat(state, chat, registration)
    choose(state, sid, "mode", ["long"])
    choose(state, sid, "verification", ["smoke"])
    return sid


def package_args(key="pkg"):
    return {"goal": "long task", "allowed_files": ["src"], "acceptance": ["done"],
            "subtasks": [{"id": "T1", "kind": "implement", "allowed_files": ["src/a.py"],
                          "acceptance": ["implemented"]}],
            "request_key": key}


def test_codex_gate_options_and_long_only(env):
    _, state, _ = env
    assert [option["value"] for option in state_module.OPTIONS["codex_gate"]] == [
        "none", "closure", "packages"]
    assert state_module.OPTIONS["codex_gate"][-1]["label"] == "包级复核 + 收尾审查"

    for mode in ("quick", "standard"):
        sid = open_chat(state, "mode-" + mode)
        choose(state, sid, "mode", [mode])
        if mode == "standard":
            choose(state, sid, "verification", ["smoke"])
        out = state.request(sid, "codex_gate", ["packages"], "text")
        assert out == {"status": "SKIPPED", "reason": "MODE_DOES_NOT_REQUIRE_QUESTION"}
    investigate = open_chat(state, "investigate")
    choose(state, investigate, "mode", ["investigate"])
    assert state.request(investigate, "codex_gate", ["packages"], "text")["status"] == "SKIPPED"

    sid = open_long(state, "long-options")
    with pytest.raises(ScsError) as err:
        state.request(sid, "codex_gate", ["yes"], "text")
    assert err.value.code == "CHOICE_INVALID"
    with pytest.raises(ScsError) as err:
        state.request(sid, "codex_gate", ["none", "packages"], "text")
    assert err.value.code == "CHOICE_INVALID"
    q = state.request(sid, "codex_gate", ["packages"], "text")
    assert q["created"] is True and q["kind"] == "codex_gate"
    assert json.loads(q["recommendation"]) == ["packages"]
    assert state.get(sid)["codex_gate"] is None
    state.submit(sid, q["id"], "accept", ["packages"])
    assert state.get(sid)["codex_gate"] == "packages"


def test_long_codex_review_redirects_but_standard_keeps_it(env):
    _, state, _ = env
    sid = open_long(state, "long-review")
    assert state.request(sid, "codex_review", ["yes"], "text") == {
        "status": "SKIPPED", "reason": "USE_CODEX_GATE"}

    standard = open_chat(state, "standard-review", registration="registered")
    choose(state, standard, "mode", ["standard"])
    choose(state, standard, "verification", ["smoke"])
    q = choose(state, standard, "codex_review", ["yes"])
    assert q["kind"] == "codex_review"
    assert state.get(standard)["codex_review"] is True


def test_codex_gate_requires_verification_first(env):
    _, state, _ = env
    sid = open_chat(state, "long-order")
    choose(state, sid, "mode", ["long"])
    with pytest.raises(ScsError) as err:
        state.request(sid, "codex_gate", ["packages"], "text")
    assert err.value.code == "QUESTION_ORDER"
    choose(state, sid, "verification", ["smoke"])
    assert state.request(sid, "codex_gate", ["packages"], "text")["created"] is True


def test_work_package_create_is_blocked_until_gate_is_confirmed(env):
    _, state, work = env
    sid = open_long(state, "long-intake")
    snapshot = state.get(sid)
    assert snapshot["codex_gate"] is None
    assert snapshot["intake"] == {"ready": False, "policy": None,
                                  "source": "codex_gate", "reason": "CODEX_GATE_REQUIRED"}
    with pytest.raises(ScsError) as err:
        work.create(sid, **package_args("blocked"))
    assert err.value.code == "INTAKE_REQUIRED"
    assert err.value.details["reason"] == "CODEX_GATE_REQUIRED"

    choose(state, sid, "codex_gate", ["packages"])
    assert state.get(sid)["intake"] == {"ready": True, "policy": "packages",
                                        "source": "codex_gate", "reason": None}
    package = work.create(sid, **package_args("blocked"))
    assert package["id"] and package["review_required"] is True


@pytest.mark.parametrize("policy,review_required", [
    ("packages", True), ("closure", False), ("none", False)])
def test_confirmed_policy_controls_package_review_required(env, policy, review_required):
    _, state, work = env
    sid = open_long(state, "policy-" + policy)
    choose(state, sid, "codex_gate", [policy])
    package = work.create(sid, **package_args("policy-" + policy))
    assert package["review_required"] is review_required


def test_reopen_mode_to_standard_switches_to_codex_review(env):
    _, state, _ = env
    sid = open_long(state, "reopen-mode", registration="registered")
    choose(state, sid, "codex_gate", ["packages"])
    state.reopen(sid, "mode", explicit_user_change=True)
    choose(state, sid, "mode", ["standard"])
    # The long verification choice stays confirmed; only the mode changed.
    q = state.request(sid, "codex_review", ["yes"], "text")
    assert q["created"] is True and q["kind"] == "codex_review"
    state.submit(sid, q["id"], "accept", ["yes"])
    snapshot = state.get(sid)
    assert snapshot["mode"] == "standard" and snapshot["codex_review"] is True
    # The historical gate answer remains visible; it no longer governs intake.
    assert snapshot["codex_gate"] == "packages"
    assert snapshot["intake"]["ready"] is True
    assert snapshot["intake"]["source"] == "codex_review"


def test_legacy_long_session_without_gate_is_asked_once(env, tmp_path):
    store, state, work = env
    sid = open_chat(state, "legacy-long")
    with store.transaction(True) as con:
        con.execute("UPDATE sessions SET mode='long', verification=? WHERE id=?",
                    (json.dumps(["smoke"]), sid))
    snapshot = state.get(sid)
    assert snapshot["codex_gate"] is None
    assert snapshot["intake"]["reason"] == "CODEX_GATE_REQUIRED"
    with pytest.raises(ScsError) as err:
        work.create(sid, **package_args("legacy-blocked"))
    assert err.value.code == "INTAKE_REQUIRED"

    q = state.request(sid, "codex_gate", ["closure"], "text")
    assert q["created"] is True
    state.submit(sid, q["id"], "accept", ["closure"])
    package = work.create(sid, **package_args("legacy-blocked"))
    assert package["review_required"] is False


def test_legacy_long_with_only_codex_review_still_asks_gate(env):
    store, state, _ = env
    sid = open_chat(state, "legacy-review")
    with store.transaction(True) as con:
        con.execute("UPDATE sessions SET mode='long', verification=?, codex_review=1 WHERE id=?",
                    (json.dumps(["smoke"]), sid))
    snapshot = state.get(sid)
    assert snapshot["codex_review"] is True and snapshot["codex_gate"] is None
    assert snapshot["intake"]["reason"] == "CODEX_GATE_REQUIRED"


def test_unknown_legacy_mode_does_not_deadlock_intake(env):
    store, state, work = env
    sid = open_chat(state, "legacy-unknown")
    with store.transaction(True) as con:
        con.execute("UPDATE sessions SET mode='obsolete-mode' WHERE id=?", (sid,))
    assert state.get(sid)["intake"]["ready"] is True
    package = work.create(sid, **package_args("legacy-unknown"))
    assert package["review_required"] is False


def test_default_resources_by_subtask_kind(env):
    _, state, work = env
    sid = open_chat(state, "resources", skip=True)
    package = work.create(sid, "resources", ["src"], ["done"], [
        {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"]},
        {"id": "T", "kind": "test", "allowed_files": ["src/a.py"], "acceptance": ["t"]},
        {"id": "V", "kind": "review", "allowed_files": ["src/a.py"], "acceptance": ["v"]},
        {"id": "I", "kind": "implement", "allowed_files": ["src/a.py"], "acceptance": ["i"]},
    ], "resources")
    resources = {task["id"]: task["resources"] for task in package["subtasks"]}
    assert resources["A"] == [] and resources["T"] == [] and resources["V"] == []
    assert resources["I"] == [{"resource": "file:src/a.py", "mode": "write"}]


def test_explicit_file_read_resource_is_rejected(env):
    _, state, work = env
    sid = open_chat(state, "read-reject", skip=True)
    with pytest.raises(ScsError) as err:
        work.create(sid, "read", ["src"], ["done"], [
            {"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"], "acceptance": ["a"],
             "resources": [{"resource": "file:src/a.py", "mode": "read"}]},
        ], "read-reject")
    assert err.value.code == "RESOURCE_READ_UNSUPPORTED"


def test_legacy_stored_read_resource_is_filtered_with_warning(env):
    store, state, work = env
    sid = open_chat(state, "legacy-resource", skip=True)
    args = {"goal": "legacy resource", "allowed_files": ["src"], "acceptance": ["done"],
            "subtasks": [{"id": "A", "kind": "analyze", "allowed_files": ["src/a.py"],
                          "acceptance": ["report"]}], "request_key": "legacy-resource"}
    package = work.create(sid, **args)
    package = work.update(sid, package["id"], package["version"], "IN_PROGRESS")
    with store.transaction(True) as con:
        row = con.execute("SELECT data FROM work_packages WHERE id=?", (package["id"],)).fetchone()
        data = json.loads(row["data"])
        data["subtasks"][0]["resources"] = [{"resource": "file:src/a.py", "mode": "read"}]
        con.execute("UPDATE work_packages SET data=? WHERE id=?",
                    (encode(data), package["id"]))

    prepared = work.prepare_attempt(sid, package["id"], package["version"], "A")
    assert prepared["leases"] == []
    assert [warning["code"] for warning in prepared["warnings"]] == ["READ_LEASE_SKIPPED"]
    with store.transaction() as con:
        assert con.execute("SELECT count(*) FROM leases").fetchone()[0] == 0

    # An idempotent create retry ignores the legacy read resource on both sides.
    again = work.create(sid, **args)
    assert again["id"] == package["id"]


@pytest.mark.parametrize("ending", ["waiting", "declined", "timed_out"])
def test_long_legacy_codex_review_row_does_not_block_gate(env, ending):
    """P2: a real legacy review question row must not shadow the long-mode gate."""
    store, state, _ = env
    sid = open_chat(state, "legacy-row-" + ending, registration="registered")
    choose(state, sid, "mode", ["standard"])
    choose(state, sid, "verification", ["smoke"])
    review = state.request(sid, "codex_review", ["yes"], "text")
    assert review["created"] is True
    claimed, _ = state.claim(sid, review["id"])
    assert claimed is True
    if ending == "declined":
        state.submit(sid, review["id"], "decline")
    elif ending == "timed_out":
        state.submit(sid, review["id"], "timeout", observed_timeout=True)
    row = state.question(sid, review["id"])
    assert row["current"] == 1 and row["state"] != "CONFIRMED", row

    # Simulate an older database whose session is long but still holds the
    # closure-only review question row.
    with store.transaction(True) as con:
        con.execute("UPDATE sessions SET mode='long' WHERE id=?", (sid,))

    gate = state.request(sid, "codex_gate", ["packages"], "text")
    assert gate["created"] is True and gate["kind"] == "codex_gate"
    retired = state.question(sid, review["id"])
    assert retired["current"] == 0
    with store.transaction() as con:
        audit = con.execute(
            "SELECT event,fallback_reason FROM events WHERE question_id=? "
            "AND tool='interaction_request' ORDER BY seq DESC LIMIT 1",
            (review["id"],)).fetchone()
    assert (audit["event"], audit["fallback_reason"]) == (
        "SUPERSEDED", "LONG_MODE_USES_CODEX_GATE")
    assert state.request(sid, "codex_review", ["yes"], "text") == {
        "status": "SKIPPED", "reason": "USE_CODEX_GATE"}

    state.submit(sid, gate["id"], "accept", ["packages"])
    assert state.get(sid)["intake"] == {"ready": True, "policy": "packages",
                                        "source": "codex_gate", "reason": None}


def test_standard_legacy_codex_review_row_keeps_old_behavior(env):
    """P2: standard mode still reuses its current review row and pending check."""
    store, state, _ = env
    sid = open_chat(state, "standard-row", registration="registered")
    choose(state, sid, "mode", ["standard"])
    # A legacy session can have a verification column without a question row.
    with store.transaction(True) as con:
        con.execute("UPDATE sessions SET verification=? WHERE id=?",
                    (json.dumps(["smoke"]), sid))
    review = state.request(sid, "codex_review", ["yes"], "text")
    assert review["created"] is True
    claimed, _ = state.claim(sid, review["id"])
    assert claimed is True

    reused = state.request(sid, "codex_review", ["yes"], "text")
    assert reused["created"] is False and reused["id"] == review["id"]
    assert reused["state"] == "WAITING_USER"
    pending = state.request(sid, "verification", ["targeted"], "text")
    assert pending["status"] == "QUESTION_PENDING"
    assert pending["question_id"] == review["id"] and pending["kind"] == "codex_review"
    assert state.request(sid, "codex_gate", ["packages"], "text") == {
        "status": "SKIPPED", "reason": "MODE_DOES_NOT_REQUIRE_QUESTION"}


def test_schema_version_stays_three_after_new_operations(env):
    store, state, work = env
    with store.transaction() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
    sid = open_long(state, "schema")
    choose(state, sid, "codex_gate", ["packages"])
    work.create(sid, **package_args("schema"))
    with store.transaction() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
        columns = [row[1] for row in con.execute("PRAGMA table_info(sessions)")]
    assert len(columns) == 9
    assert "codex_gate" not in columns
