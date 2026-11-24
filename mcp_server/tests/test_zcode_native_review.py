"""ZCode native review intake, package receipts and operation gate."""

import hashlib
import json

import pytest
from mcp import Client

from scs_mcp.errors import ScsError
from scs_mcp.gate import Gate
from scs_mcp.interaction import native_question
from scs_mcp.server import create_server
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    state = State(Store(tmp_path))
    return state, state.open(str(tmp_path), "zcode", "native-review")["id"]


def confirm(state, sid, kind, value):
    question = state.request(sid, kind, [value], "native")
    assert state.submit(sid, question["id"], "accept", [value], "native")["status"] == "CONFIRMED"


def long_intake(state, sid, route=None):
    confirm(state, sid, "mode", "long")
    confirm(state, sid, "verification", "none")
    confirm(state, sid, "codex_gate", "packages")
    if route:
        confirm(state, sid, "review_route", route)


def review_package(work, sid):
    return work.create(sid, "Review a bounded change", ["src.py"], ["Reviewed"], [
        {"id": "review", "kind": "review", "allowed_files": ["src.py"], "acceptance": ["Reviewed"]},
    ], "review-native")


def write_final(root, path, text):
    raw = text.encode("utf-8")
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def receipt(subject_key, digest, agent_type, final_path, final_sha, verdict="ACCEPT"):
    return {"schema_version": 2, subject_key: digest, "agent_type": agent_type,
            "agent_id": "host-agent-42", "model": "unobserved", "status": "completed",
            "verdict": verdict, "final_message": {"path": final_path, "sha256": final_sha}}


def test_native_long_intake_waits_for_verification_timing_and_real_route(native):
    state, sid = native
    assert state.request(sid, "codex_review", ["yes"], "native")["status"] == "SKIPPED"
    confirm(state, sid, "mode", "long")
    for prerequisite, reason in [("verification", "VERIFICATION_REQUIRED"), ("codex_gate", "CODEX_GATE_REQUIRED")]:
        with pytest.raises(ScsError) as err:
            state.request(sid, "review_route", ["gpt"], "native")
        assert err.value.code == "QUESTION_ORDER"
        assert state.get(sid)["intake"]["reason"] == reason
        confirm(state, sid, prerequisite, "targeted" if prerequisite == "verification" else "closure")
    assert state.request(sid, "codex_review", ["yes"], "native")["status"] == "SKIPPED"
    snap = state.get(sid)
    assert snap["codex_review"] is None and snap["codex_gate"] == "closure"
    assert snap["intake"] == {"ready": False, "policy": "closure", "source": "review_route",
                              "reason": "REVIEW_ROUTE_REQUIRED"}
    q = state.request(sid, "review_route", ["gpt"], "native")
    shown = native_question(state.question(sid, q["id"]), None)
    assert [(item["value"], item["desc"]) for item in shown["options"]] == [
        ("gpt", "由宿主启动 固定审阅员"), ("inherit", "由宿主启动 继承审阅员")]
    with pytest.raises(ScsError) as err:
        review_package(Work(state.store), sid)
    assert err.value.code == "INTAKE_REQUIRED"
    assert state.submit(sid, q["id"], "timeout", observed_timeout=True)["status"] == "TIMED_OUT"
    assert state.get(sid)["review_route"] is None
    assert not state.get(sid)["intake"]["ready"]
    state.reopen(sid, "review_route")
    with pytest.raises(ScsError) as err:
        state.submit(sid, q["id"], "accept", ["gpt"])
    assert err.value.code == "LATE_REPLY"
    confirm(state, sid, "review_route", "inherit")
    assert state.get(sid)["review_route"] == "inherit"
    assert state.get(sid)["intake"]["ready"]


@pytest.mark.parametrize("mode", ["quick", "investigate", "standard"])
def test_native_short_intake_never_asks_special_review(native, mode):
    state, sid = native
    confirm(state, sid, "mode", mode)
    state.open(str(state.store.root), "zcode", "native-review", review_registration="registered")
    for kind, choice in [("codex_review", "yes"), ("review_route", "gpt"), ("codex_gate", "packages")]:
        assert state.request(sid, kind, [choice], "native")["status"] == "SKIPPED"
    if mode == "standard":
        assert state.get(sid)["intake"]["reason"] == "VERIFICATION_REQUIRED"
        confirm(state, sid, "verification", "targeted")
    else:
        assert state.request(sid, "verification", ["targeted"], "native")["status"] == "SKIPPED"
    snap = state.get(sid)
    assert snap["intake"] == {"ready": True, "policy": None, "source": "not_required", "reason": None}
    assert snap["codex_review"] is None and snap["codex_gate"] is None and snap["review_route"] is None


@pytest.mark.parametrize(("kind", "choice", "question_state"), [
    ("codex_review", "yes", "WAITING_USER"),
    ("codex_review", "yes", "CONFIRMED"),
    ("codex_gate", "packages", "WAITING_USER"),
    ("review_route", "gpt", "WAITING_USER"),
    ("review_route", "gpt", "CONFIRMED"),
])
def test_stale_native_review_question_is_inert_in_short_mode(native, kind, choice, question_state):
    state, sid = native
    confirm(state, sid, "mode", "standard")
    with state.store.transaction(True) as con:
        answer = json.dumps([choice]) if question_state == "CONFIRMED" else None
        con.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("old-review", sid, kind, 1, question_state, "native", json.dumps([choice]), answer, "old", "old"))
        if kind == "codex_review" and answer:
            con.execute("UPDATE sessions SET codex_review=1 WHERE id=?", (sid,))
    snap = state.get(sid)
    assert snap["question_states"][kind] == "NOT_ASKED" and not snap["unresolved"]
    assert snap["codex_review"] is None and snap["review_route"] is None
    assert not state.question(sid, "old-review")["current"]
    with pytest.raises(ScsError) as err:
        state.submit(sid, "old-review", "accept", [choice], "native")
    assert err.value.code == "LATE_REPLY"
    confirm(state, sid, "verification", "targeted")
    assert state.get(sid)["intake"]["ready"]
    assert state.request(sid, kind, [choice], "native")["status"] == "SKIPPED"
    state.reopen(sid, "mode", explicit_user_change=True)
    confirm(state, sid, "mode", "long")
    confirm(state, sid, "codex_gate", "packages")
    assert state.get(sid)["intake"]["reason"] == "REVIEW_ROUTE_REQUIRED"
    q = state.request(sid, "review_route", ["gpt"], "native")
    assert q["created"] and q["id"] != "old-review"


@pytest.mark.parametrize(("route", "agent_type", "english"), [
    ("gpt", "固定审阅员", "review-gpt"), ("inherit", "继承审阅员", "review-inherit")])
def test_package_receipt_pins_route_host_and_current_subject(native, tmp_path, route, agent_type, english):
    state, sid = native
    long_intake(state, sid, route)
    (tmp_path / "src.py").write_text("value = 1\n", encoding="utf-8")
    work = Work(state.store)
    package = review_package(work, sid)
    pid = package["id"]
    assert package["review_required"] is True
    package = work.update(sid, pid, package["version"], "IN_PROGRESS")
    version = package["version"]
    prepared = work.prepare_attempt(sid, pid, version, "review")
    attempt = prepared["attempt"]["id"]
    assert prepared["agent_type"] == prepared["input_snapshot"]["agent_type"] == agent_type
    assert prepared["input_snapshot"]["review_route"] == route
    work.update_attempt(sid, attempt, version, "LAUNCHING", host_agent_id="host-agent-42")
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt, version, "RUNNING", host_agent_id="different-agent")
    assert err.value.code == "REVIEW_AGENT_MISMATCH"
    folder = prepared["evidence_dir"]
    final_sha = write_final(tmp_path, folder + "/raw.md", "Review text\n**结论：ACCEPT**\n")
    manifest = receipt("review_subject", prepared["review_subject"], agent_type, "raw.md", final_sha)
    path = folder + "/manifest.json"

    def publish(value):
        sha = write_final(tmp_path, path, json.dumps(value, ensure_ascii=False))
        return {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]}

    for invalid in ({**manifest, "agent_type": english}, {**manifest, "agent_id": "different-agent"}):
        result = publish(invalid)
        assert work.evidence(sid, pid, 1, "review", path)["error_code"] == "REVIEW_AGENT_MISMATCH"
        with pytest.raises(ScsError) as err:
            work.update_attempt(sid, attempt, version, "ENDED", result)
        assert err.value.code == "EVIDENCE_INVALID"
    result = publish(manifest)
    assert work.evidence(sid, pid, 1, "review", path)["review"]["agent_type"] == agent_type
    ended = work.update_attempt(sid, attempt, version, "ENDED", result)
    assert ended["task_status"] == "PASS"
    ready = work.update(sid, pid, ended["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"
    (tmp_path / "src.py").write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(ScsError) as err:
        work.update(sid, pid, ready["version"], "COMPLETED", close_reason="accepted")
    assert err.value.code == "REVIEW_STALE"
    with pytest.raises(ScsError) as err:
        work.export(sid, pid, ready["version"])
    assert err.value.code == "REVIEW_STALE"


def test_operation_gate_fails_closed_and_reject_is_terminal(native, tmp_path):
    state, sid = native
    long_intake(state, sid)
    gate = Gate(state.store)
    operation = {"session_id": sid, "operation_id": "op", "command": "remove generated outputs",
                 "cwd": str(tmp_path), "targets": ["generated"]}
    missing = gate.check(**operation)
    assert missing["status"] == "MISSING" and not missing["approved"]
    path = "docs/gate.md"
    sha = write_final(tmp_path, path, "Review text\n结论: REJECT\n")
    manifest = receipt("operation_digest", missing["operation_digest"], "继承审阅员", path, sha, "REJECT")
    args = {**operation, "impact": "Generated outputs only", "backup": "From source",
            "rollback": "Regenerate outputs", "verdict": "REJECT", "user_confirmed": False}
    with pytest.raises(ScsError) as err:
        gate.record(**args, review_receipt=manifest)
    assert err.value.code == "REVIEW_ROUTE_REQUIRED"
    confirm(state, sid, "review_route", "inherit")
    for invalid, code in [({**manifest, "agent_type": "review-inherit"}, "REVIEW_AGENT_MISMATCH"),
                          ({**manifest, "operation_digest": "0" * 64}, "REVIEW_STALE"),
                          ({**manifest, "final_message": {"path": path, "sha256": "0" * 64}},
                           "REVIEW_FINAL_MESSAGE_HASH_MISMATCH")]:
        with pytest.raises(ScsError) as err:
            gate.record(**args, review_receipt=invalid)
        assert err.value.code == code
    assert gate.check(**operation)["status"] == "MISSING"
    saved = gate.record(**args, review_receipt=manifest)
    assert gate.record(**args, review_receipt=manifest)["event_seq"] == saved["event_seq"]
    assert gate.check(**operation)["status"] == "REJECTED"
    with pytest.raises(ScsError) as err:
        gate.record(**{**args, "verdict": "ACCEPT", "user_confirmed": True})
    assert err.value.code == "GATE_REJECTED"


async def test_native_operation_receipt_is_exposed_over_the_existing_mcp_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    async with Client(create_server(tmp_path, question_channel="native"), mode="legacy") as client:
        async def call(name, **args):
            return (await client.call_tool(name, args)).structured_content

        opened = await call("session_open", explicit_skip=True)
        assert opened["native_review"] is True
        sid = opened["id"]
        async def ask(kind, choice):
            return await call("interaction_request", session_id=sid, kind=kind, recommendation=[choice])

        async def confirm_native(kind, choice):
            question = await ask(kind, choice)
            assert question["status"] == "NATIVE_QUESTION_REQUIRED"
            answer = await call("interaction_submit", session_id=sid, question_id=question["question_id"],
                                action="accept", choices=[choice], source="native")
            assert answer["status"] == "CONFIRMED"

        operation = {"session_id": sid, "operation_id": "sdk-op", "command": "remove generated outputs",
                     "cwd": str(tmp_path), "targets": ["generated"]}
        check = await call("operation_gate_check", **operation)
        path = "docs/gate-accept.md"
        sha = write_final(tmp_path, path, "独立审查\n结论: ACCEPT\n")
        manifest = receipt("operation_digest", check["operation_digest"], "固定审阅员", path, sha)
        args = {**operation, "impact": "Generated outputs only", "backup": "From source",
                "rollback": "Regenerate outputs", "verdict": "ACCEPT"}
        assert (await ask("review_route", "gpt"))["status"] == "SKIPPED"
        assert (await call("operation_gate_record", **args, user_confirmed=True,
                           review_receipt=manifest))["error_code"] == "REVIEW_ROUTE_REQUIRED"
        assert (await call("operation_gate_check", **operation))["status"] == "MISSING"
        assert (await call("session_get", session_id=sid))["mode"] == "quick"
        reopened = await call("interaction_reopen", session_id=sid, kind="mode", explicit_user_change=True)
        assert reopened["status"] == "REOPENED"
        await confirm_native("mode", "long")
        await confirm_native("verification", "none")
        await confirm_native("codex_gate", "none")  # No package or closure review; an operation still needs its own review.
        assert (await call("session_get", session_id=sid))["review_route"] is None
        await confirm_native("review_route", "gpt")
        assert (await call("operation_gate_record", **args, user_confirmed=True,
                           report_path=path))["error_code"] == "REVIEW_MANIFEST_INVALID"
        assert (await call("operation_gate_record", **args, user_confirmed=False,
                           review_receipt=manifest))["error_code"] == "GATE_INVALID"
        saved = await call("operation_gate_record", **args, user_confirmed=True, review_receipt=manifest)
        assert saved["status"] == "OK" and saved["report_sha256"] == sha
        (tmp_path / path).unlink()
        check = await call("operation_gate_check", **operation)
        assert check["status"] == "APPROVED" and check["user_confirmation_still_required"]
        assert (await call("operation_gate_check", **{**operation, "targets": ["other"]}))["status"] == "STALE"
        reopened = await call("interaction_reopen", session_id=sid, kind="mode", explicit_user_change=True)
        assert reopened["status"] == "REOPENED"
        # Even before a new mode request retires the old row, the prior approval is unusable.
        assert (await call("operation_gate_check", **operation))["status"] == "STALE"
        await confirm_native("mode", "quick")
        assert (await call("operation_gate_check", **operation))["status"] == "STALE"


def test_existing_legacy_gate_record_is_not_a_native_approval(tmp_path, monkeypatch):
    monkeypatch.delenv("SCS_NATIVE_REVIEW", raising=False)
    legacy = Store(tmp_path)
    sid = State(legacy).open(str(tmp_path), "legacy", "op", explicit_skip=True)["id"]
    path = "docs/legacy.md"
    write_final(tmp_path, path, "Legacy report\n结论: ACCEPT\n")
    operation = {"session_id": sid, "operation_id": "legacy-op", "command": "remove generated outputs",
                 "cwd": str(tmp_path), "targets": ["generated"]}
    Gate(legacy).record(**operation, impact="Generated only", backup="Source", rollback="Regenerate",
                        verdict="ACCEPT", user_confirmed=True, report_path=path)
    assert Gate(legacy).check(**operation)["status"] == "APPROVED"
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    native = Store(tmp_path)
    state = State(native)
    state.reopen(sid, "mode", explicit_user_change=True)
    long_intake(state, sid, "gpt")
    assert Gate(native).check(**operation)["status"] == "STALE"
