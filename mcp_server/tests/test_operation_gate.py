"""Protocol-level destructive-operation gate: records, digest checks, terminals."""

import hashlib
import json
import os

import pytest

from scs_mcp.errors import ScsError
from scs_mcp.gate import Gate
from scs_mcp.state import State
from scs_mcp.storage import Store


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    sid = state.open(str(tmp_path), "test", "gate", explicit_skip=True)["id"]
    (tmp_path / "workdir").mkdir()
    (tmp_path / "reports").mkdir()
    return tmp_path, store, state, sid, Gate(store)


def write_report(tmp_path, relative="reports/gate.md", conclusion="ACCEPT",
                 body="report body\n"):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Gate report\n\n{body}结论: {conclusion}\n", encoding="utf-8")
    return relative


def write_raw(tmp_path, relative, text):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


def record(gate, tmp_path, sid, **overrides):
    args = {"session_id": sid, "operation_id": "op-1", "command": "rm -rf build",
            "cwd": str(tmp_path), "targets": ["build"], "impact": "deletes build outputs",
            "backup": "archive at /backup/build.tar", "rollback": "restore archive",
            "verdict": "ACCEPT", "user_confirmed": True, "report_path": "reports/gate.md"}
    args.update(overrides)
    return gate.record(**args)


def event_count(store):
    with store.transaction() as con:
        return con.execute("SELECT count(*) FROM events").fetchone()[0]


def test_accept_requires_report_and_confirmation(env):
    tmp_path, _, _, sid, gate = env
    write_report(tmp_path)
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, report_path=None)
    assert err.value.code == "GATE_INVALID"
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, user_confirmed=False)
    assert err.value.code == "GATE_INVALID"
    write_report(tmp_path, "reports/reject.md", "REJECT")
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, report_path="reports/reject.md")
    assert err.value.code == "GATE_INVALID"

    out = record(gate, tmp_path, sid)
    assert out["status"] == "OK"
    assert out["recorded"] is True and out["idempotent"] is False
    assert out["verdict"] == "ACCEPT"
    assert out["protocol_only"] is True and out["execution_intercepted"] is False
    assert out["report_sha256"] == hashlib.sha256(
        (tmp_path / "reports/gate.md").read_bytes()).hexdigest()


def test_check_is_read_only_and_keeps_requiring_user_confirmation(env):
    tmp_path, store, _, sid, gate = env
    write_report(tmp_path)
    out = record(gate, tmp_path, sid)
    before = event_count(store)
    approved = gate.check(sid, "op-1")
    assert approved["status"] == "APPROVED" and approved["approved"] is True
    assert approved["user_confirmation_still_required"] is True
    assert approved["execution_intercepted"] is False
    assert event_count(store) == before

    # The report sha256 is fixed at record time; check never re-reads the file.
    (tmp_path / "reports/gate.md").unlink()
    assert gate.check(sid, "op-1")["status"] == "APPROVED"
    assert out["operation_digest"] == gate.check(sid, "op-1")["operation_digest"]


def test_reject_is_terminal_for_the_same_operation_digest(env):
    tmp_path, _, _, sid, gate = env
    write_report(tmp_path, "reports/reject.md", "REJECT")
    args = {"operation_id": "reject-op", "verdict": "REJECT",
            "report_path": "reports/reject.md", "user_confirmed": False}
    first = record(gate, tmp_path, sid, **args)
    assert first["verdict"] == "REJECT" and first["recorded"] is True
    replay = record(gate, tmp_path, sid, **args)
    assert replay["idempotent"] is True and replay["recorded"] is False
    assert replay["event_seq"] == first["event_seq"]

    write_report(tmp_path, "reports/accept.md", "ACCEPT")
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, operation_id="reject-op", verdict="ACCEPT",
               report_path="reports/accept.md")
    assert err.value.code == "GATE_REJECTED"
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, operation_id="reject-op",
               verdict="UNAVAILABLE_OVERRIDE", user_confirmed=True,
               reason="codex binary is unavailable here")
    assert err.value.code == "GATE_REJECTED"
    checked = gate.check(sid, "reject-op")
    assert checked["status"] == "REJECTED" and checked["approved"] is False
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, operation_id="reject-op", command="rm -rf other")
    assert err.value.code == "OPERATION_CONFLICT"


def test_unavailable_override_is_never_approved(env):
    tmp_path, _, _, sid, gate = env
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, verdict="UNAVAILABLE_OVERRIDE", report_path=None,
               user_confirmed=True)
    assert err.value.code == "GATE_INVALID"
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, verdict="UNAVAILABLE_OVERRIDE", report_path=None,
               user_confirmed=True, reason="too short")
    assert err.value.code == "GATE_INVALID"
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, verdict="UNAVAILABLE_OVERRIDE", report_path=None,
               user_confirmed=False, reason="codex is unavailable in this environment")
    assert err.value.code == "GATE_INVALID"

    out = record(gate, tmp_path, sid, verdict="UNAVAILABLE_OVERRIDE", report_path=None,
                 user_confirmed=True, reason="codex is unavailable in this environment")
    assert out["verdict"] == "UNAVAILABLE_OVERRIDE" and out["report_sha256"] is None
    checked = gate.check(sid, "op-1")
    assert checked["status"] == "UNAVAILABLE_OVERRIDE"
    assert checked["approved"] is False

    write_report(tmp_path, "reports/accept.md", "ACCEPT")
    accepted = record(gate, tmp_path, sid, verdict="ACCEPT", user_confirmed=True,
                      report_path="reports/accept.md")
    assert accepted["status"] == "OK"
    assert gate.check(sid, "op-1")["status"] == "APPROVED"

    record(gate, tmp_path, sid, operation_id="accepted-op",
           report_path="reports/accept.md")
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, operation_id="accepted-op",
               verdict="UNAVAILABLE_OVERRIDE", report_path=None, user_confirmed=True,
               reason="codex disappeared after acceptance")
    assert err.value.code == "OPERATION_CONFLICT"


def test_digest_and_baseline_changes_are_stale(env):
    tmp_path, _, _, sid, gate = env
    write_report(tmp_path)
    out = record(gate, tmp_path, sid, baseline={"head": "ABCDEF1"})
    assert out["baseline"] == {"head": "abcdef1"}
    assert gate.check(sid, "op-1")["status"] == "APPROVED"

    changed = gate.check(sid, "op-1", command="rm -rf build2")
    assert changed["status"] == "STALE" and changed["approved"] is False
    assert "COMMAND_MISMATCH" in changed["reasons"]
    assert changed["expected_digest"] == out["operation_digest"]
    assert changed["computed_digest"] != changed["expected_digest"]

    cwd_changed = gate.check(sid, "op-1", cwd=str(tmp_path / "workdir"))
    assert "CWD_MISMATCH" in cwd_changed["reasons"]
    target_changed = gate.check(sid, "op-1", targets=["archive"])
    assert "TARGETS_MISMATCH" in target_changed["reasons"]
    baseline_changed = gate.check(sid, "op-1", baseline={"head": "abcdef2"})
    assert "BASELINE_MISMATCH" in baseline_changed["reasons"]
    invalid_baseline = gate.check(sid, "op-1", baseline={"head": "zz"})
    assert invalid_baseline["status"] == "STALE"
    assert invalid_baseline["reasons"] == ["BASELINE_INVALID"]


def test_same_operation_id_with_another_digest_conflicts(env):
    tmp_path, _, _, sid, gate = env
    write_report(tmp_path)
    record(gate, tmp_path, sid)
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, command="rm -rf other", targets=["other"])
    assert err.value.code == "OPERATION_CONFLICT"
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, baseline={"head": "abcdef1"})
    assert err.value.code == "OPERATION_CONFLICT"


def test_missing_is_scoped_to_the_session(env):
    tmp_path, _, state, sid, gate = env
    missing = gate.check(sid, "unknown-op")
    assert missing["status"] == "MISSING" and missing["approved"] is False
    write_report(tmp_path)
    record(gate, tmp_path, sid, operation_id="owned-op")
    other = state.open(str(tmp_path), "test", "other")["id"]
    assert gate.check(other, "owned-op")["status"] == "MISSING"
    with pytest.raises(ScsError) as err:
        gate.check("S-missing", "owned-op")
    assert err.value.code == "SESSION_NOT_FOUND"


def test_baseline_shapes_are_validated_and_normalized(env):
    tmp_path, _, _, sid, gate = env
    write_report(tmp_path)
    for index, baseline in enumerate((
            {}, {"head": "xyz"}, {"head": "abcdef1", "files": {}},
            {"files": {"src/a.py": "not-a-digest"}}, {"unknown": "value"})):
        with pytest.raises(ScsError) as err:
            record(gate, tmp_path, sid, operation_id=f"bad-baseline-{index}",
                   baseline=baseline)
        assert err.value.code == "GATE_INVALID"
    out = record(gate, tmp_path, sid, operation_id="files-baseline",
                 baseline={"files": {"src/a.py": "A" * 64}})
    assert out["baseline"] == {"files": {"src/a.py": "a" * 64}}


def test_cwd_targets_and_report_paths_are_bounded(env):
    tmp_path, _, _, sid, gate = env
    write_report(tmp_path)
    for bad_cwd in ("relative/path", str(tmp_path / "missing"), str(tmp_path.parent)):
        with pytest.raises(ScsError) as err:
            record(gate, tmp_path, sid, cwd=bad_cwd)
        assert err.value.code == "GATE_INVALID"
    for targets in ([], ["/etc/passwd"], ["../outside"], ["build", "build"],
                    ["device:../bad"], [f"t{index}" for index in range(101)]):
        with pytest.raises(ScsError) as err:
            record(gate, tmp_path, sid, targets=targets)
        assert err.value.code == "GATE_INVALID"
    for bad_report in ("/etc/passwd", "../report.md", "reports/missing.md"):
        with pytest.raises(ScsError) as err:
            record(gate, tmp_path, sid, report_path=bad_report)
        assert err.value.code == "GATE_INVALID"

    device = record(gate, tmp_path, sid, operation_id="device-target",
                    targets=["device:board-1"])
    assert device["targets"] == ["device:board-1"]


def test_report_conclusion_must_be_unique_and_text(env):
    tmp_path, _, _, sid, gate = env
    write_raw(tmp_path, "reports/bold.md", "**结论: ACCEPT**\n")
    assert record(gate, tmp_path, sid, operation_id="bold",
                  report_path="reports/bold.md")["verdict"] == "ACCEPT"
    write_raw(tmp_path, "reports/fullwidth.md", "结论：ACCEPT\n")
    assert record(gate, tmp_path, sid, operation_id="fullwidth",
                  report_path="reports/fullwidth.md")["verdict"] == "ACCEPT"
    for operation_id, relative, text in (
            ("no-conclusion", "reports/none.md", "nothing here\n"),
            ("two-conclusions", "reports/two.md", "结论: ACCEPT\n结论: ACCEPT\n"),
            ("mismatch", "reports/mismatch.md", "结论: REJECT\n")):
        write_raw(tmp_path, relative, text)
        with pytest.raises(ScsError) as err:
            record(gate, tmp_path, sid, operation_id=operation_id,
                   report_path=relative)
        assert err.value.code == "GATE_INVALID"

    (tmp_path / "reports/binary.md").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, operation_id="binary",
               report_path="reports/binary.md")
    assert err.value.code == "GATE_INVALID"

    target = tmp_path / "reports/target.md"
    target.write_text("结论: ACCEPT\n", encoding="utf-8")
    os.symlink(target, tmp_path / "reports/link.md")
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, operation_id="symlink",
               report_path="reports/link.md")
    assert err.value.code == "GATE_INVALID"


def test_event_summary_is_bounded_and_never_stores_report_body(env):
    tmp_path, store, _, sid, gate = env
    write_report(tmp_path, "reports/secret.md", "ACCEPT",
                 body="PRIVATE-REPORT-BODY\n")
    out = record(gate, tmp_path, sid, operation_id="bounded",
                 report_path="reports/secret.md")
    with store.transaction() as con:
        row = con.execute("SELECT event FROM events WHERE tool='operation_gate' AND seq=?",
                          (out["event_seq"],)).fetchone()
    assert "PRIVATE-REPORT-BODY" not in row["event"]
    assert len(row["event"].encode("utf-8")) <= 16 * 1024
    payload = json.loads(row["event"])
    assert payload["schema"] == 1
    assert payload["protocol_only"] is True
    assert payload["operation_digest"] == out["operation_digest"]
    assert payload["report_sha256"] == out["report_sha256"]

    baseline = {"files": {f"f{index:03d}_" + "x" * 40: "a" * 64 for index in range(200)}}
    with pytest.raises(ScsError) as err:
        record(gate, tmp_path, sid, operation_id="oversize", baseline=baseline,
               report_path="reports/secret.md", command="x" * 4000,
               impact="i" * 2000, backup="b" * 2000, rollback="r" * 2000,
               reason="r" * 2000)
    assert err.value.code == "GATE_INVALID"
