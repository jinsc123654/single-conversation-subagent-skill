"""The main protocol uses registered execution agents and a fixed ZCode reviewer."""

import hashlib
import json

import pytest
from mcp import Client

from scs_mcp.errors import ScsError
from scs_mcp.gate import GATE_TOOL, Gate
from scs_mcp.server import create_server
from scs_mcp.storage import Store, encode, now
from scs_mcp.work import Work

LEGACY_REVIEWER = "固定审阅员"


@pytest.fixture
def zcode(tmp_path, monkeypatch):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    return create_server(tmp_path, question_channel="native", host="zcode")


def write_evidence(root, relative, content):
    path = root / relative
    path.write_text(content, encoding="utf-8")
    return {"path": relative, "sha256": hashlib.sha256(content.encode()).hexdigest()}


def receipt(key, digest, final, *, agent_type="scs-fixed-reviewer", agent_id="reviewer-1", verdict="ACCEPT"):
    return {"schema_version": 2, key: digest, "agent_type": agent_type, "agent_id": agent_id,
            "model": "unobserved", "status": "completed", "verdict": verdict, "final_message": final}


def test_journey_execution_profile_routes_to_device_agent(zcode, tmp_path):
    state = zcode.state
    _, _, session = zcode.roots.open(str(tmp_path), "zcode", "journey", "registered", False)
    sid = session["id"]
    for kind, answer in (("mode", "long"), ("verification", "journey"), ("codex_gate", "closure")):
        question = state.request(sid, kind, [answer], "native")
        assert state.submit(sid, question["id"], "accept", [answer], "native")["status"] == "CONFIRMED"
    (tmp_path / "device.py").write_text("VALUE = 1\n")
    work = zcode.roots.work(sid)
    package = work.create(
        sid, "Device journey", ["device.py"], ["Observed"],
        [{"id": "J1", "kind": "test", "execution_profile": "journey", "allowed_files": ["device.py"], "acceptance": ["Observed"],
          "resources": [{"resource": "device:test-phone", "mode": "write"}]}],
        "journey")
    version = work.update(sid, package["id"], package["version"], "IN_PROGRESS")["version"]
    attempt = work.prepare_attempt(sid, package["id"], version, "J1", execution_profile="journey")
    assert attempt["agent_type"] == attempt["attempt"]["input_snapshot"]["agent_type"] == "scs-device-verifier"
    assert attempt["input_snapshot"]["execution_profile"] == "journey"
    assert any(lease["resource"] == "device:test-phone" for lease in attempt["leases"])


def test_analyze_and_test_attempts_freeze_ascii_routes(zcode, tmp_path):
    _, _, session = zcode.roots.open(str(tmp_path), "zcode", "routing", "unregistered", True)
    sid = session["id"]
    work = zcode.roots.work(sid)
    package = work.create(sid, "Inspect routing", ["sample.txt"], ["Checked"], [
        {"id": kind, "kind": kind, "allowed_files": ["sample.txt"], "acceptance": ["Checked"]}
        for kind in ("analyze", "test")
    ], "routing")
    batch = work.prepare_attempts(sid, package["id"], package["version"], ["analyze", "test"])
    for item, route in zip(batch["attempts"], ("scs-scout", "scs-test-verifier"), strict=True):
        assert item["agent_type"] == item["attempt"]["input_snapshot"]["agent_type"] == route


def test_parallel_attempts_and_fixed_reviewer_proof(zcode, tmp_path):
    state = zcode.state
    _, _, session = zcode.roots.open(str(tmp_path), "zcode", "parallel", "registered", False)
    sid = session["id"]
    for kind, answer in (("mode", "long"), ("verification", "targeted"), ("codex_gate", "closure")):
        question = state.request(sid, kind, [answer], "native")
        assert state.submit(sid, question["id"], "accept", [answer], "native")["status"] == "CONFIRMED"
    assert state.get(sid)["intake"]["ready"]
    (tmp_path / "left.py").write_text("left = 1\n")
    (tmp_path / "right.py").write_text("right = 2\n")
    work = zcode.roots.work(sid)
    tasks = [{"id": key, "kind": "implement", "allowed_files": [key + ".py"], "acceptance": ["Inspected"]}
             for key in ("left", "right")]
    tasks.append({"id": "review", "kind": "review", "allowed_files": ["left.py", "right.py"],
                  "acceptance": ["Reviewed"], "depends_on": ["left", "right"]})
    package = work.create(sid, "Independent changes", ["left.py", "right.py"], ["Reviewed"], tasks, "parallel")
    pid = package["id"]
    version = work.update(sid, pid, package["version"], "IN_PROGRESS")["version"]
    batch = work.prepare_attempts(sid, pid, version, ["left", "right"], ["left-1", "right-1"])
    assert [item["agent_type"] for item in batch["attempts"]] == ["scs-implementer", "scs-implementer"]
    assert all(item["attempt"]["input_snapshot"]["agent_type"] == "scs-implementer" for item in batch["attempts"])
    with pytest.raises(ScsError, match="quiescent") as blocked:
        work.prepare_attempt(sid, pid, version, "review")
    assert blocked.value.code == "REVIEW_WINDOW_ACTIVE"
    updates = [{"attempt_id": item["attempt"]["id"], "result": {"status": "PASS", "evidence": [
        write_evidence(tmp_path, item["evidence_dir"] + "/work.md", "Implementation report\n")]}}
        for item in batch["attempts"]]
    version = work.update_attempts(sid, updates, "batch-complete")["version"]
    with pytest.raises(ScsError) as automatic:
        work.prepare_attempt(sid, pid, version, "review")
    assert automatic.value.code == "REVIEW_MANUAL_REQUIRED"
    review = work.prepare_attempt(sid, pid, version, "review", user_requested=True)
    assert review["agent_type"] == review["attempt"]["input_snapshot"]["agent_type"] == "scs-fixed-reviewer"
    aid = review["attempt"]["id"]
    final = write_evidence(tmp_path, review["evidence_dir"] + "/final-message.md", "Reviewed\n结论: ACCEPT\n")
    final["path"] = "final-message.md"
    manifest_path = review["evidence_dir"] + "/manifest.json"
    manifest = receipt("review_subject", review["review_subject"], final)

    def end(manifest, agent_id):
        evidence = write_evidence(tmp_path, manifest_path, json.dumps(manifest, ensure_ascii=False))
        return work.update_attempt(sid, aid, version, "ENDED", {"status": "PASS", "evidence": [evidence]},
                                   host_agent_id=agent_id)

    for role in ("scs-inherited-reviewer", LEGACY_REVIEWER):
        with pytest.raises(ScsError) as wrong_role:
            end({**manifest, "agent_type": role}, "reviewer-1")
        assert wrong_role.value.code == "REVIEW_AGENT_MISMATCH"
    with pytest.raises(ScsError) as wrong_host:
        end(manifest, "wrong-id")
    assert wrong_host.value.code == "REVIEW_AGENT_MISMATCH"
    with pytest.raises(ScsError) as missing_host:
        end(manifest, None)
    assert missing_host.value.code == "REVIEW_AGENT_REQUIRED"
    finished = end(manifest, "reviewer-1")
    evidence = work.evidence(sid, pid, review["revision"], "review", manifest_path)
    assert evidence["status"] == "VERIFIED"
    assert work.update(sid, pid, finished["version"], "REVIEW_READY")["state"] == "REVIEW_READY"


def test_persisted_legacy_review_attempt_requires_and_retains_its_original_role(zcode, tmp_path, monkeypatch):
    _, state, session = zcode.roots.open(str(tmp_path), "zcode", "legacy-review", "unregistered", True)
    sid = session["id"]
    work = zcode.roots.work(sid)
    package = work.create(sid, "Finish legacy review", ["sample.txt"], ["Reviewed"], [
        {"id": "review", "kind": "review", "allowed_files": ["sample.txt"], "acceptance": ["Reviewed"]}
    ], "legacy-review")
    pid = package["id"]
    version = work.update(sid, pid, package["version"], "IN_PROGRESS")["version"]
    review = work.prepare_attempt(sid, pid, version, "review", host_agent_id="reviewer-1", user_requested=True)
    aid = review["attempt"]["id"]
    # Build a persisted active attempt as written by a pre-migration server.
    with state.store.transaction(True) as con:
        con.execute("UPDATE work_attempts SET input_snapshot=? WHERE id=?",
                    (encode({**review["input_snapshot"], "agent_type": LEGACY_REVIEWER}), aid))
        data = json.loads(con.execute("SELECT data FROM work_packages WHERE id=?", (pid,)).fetchone()[0])
        data.update(review_required=True, native_review_required=True)
        con.execute("UPDATE work_packages SET data=? WHERE id=?", (encode(data), pid))
    work = Work(Store(tmp_path))
    final = write_evidence(tmp_path, review["evidence_dir"] + "/final.md", "结论: ACCEPT\n")
    final["path"] = "final.md"
    path = review["evidence_dir"] + "/manifest.json"
    manifest = receipt("review_subject", review["review_subject"], final)
    evidence = write_evidence(tmp_path, path, encode(manifest))
    assert work.evidence(sid, pid, 1, "review", path)["error_code"] == "REVIEW_AGENT_MISMATCH"
    with pytest.raises(ScsError) as wrong_role:
        work.update_attempt(sid, aid, version, "ENDED", {"status": "PASS", "evidence": [evidence]})
    assert wrong_role.value.code == "REVIEW_AGENT_MISMATCH"

    evidence = write_evidence(tmp_path, path, encode({**manifest, "agent_type": LEGACY_REVIEWER}))
    verified = work.evidence(sid, pid, 1, "review", path)
    assert verified["status"] == "VERIFIED" and verified["review"]["agent_type"] == LEGACY_REVIEWER
    ended = work.update_attempt(sid, aid, version, "ENDED", {"status": "PASS", "evidence": [evidence]})
    metadata = work.get(sid, pid)["subtasks"][0]["review"]
    assert metadata["agent_type"] == LEGACY_REVIEWER and metadata["agent_id"] == "reviewer-1"
    assert work.evidence(sid, pid, 1, "review", path)["status"] == "VERIFIED"

    # The existing non-native acceptance path also revalidates stored native reports.
    monkeypatch.delenv("SCS_NATIVE_REVIEW")
    work = Work(Store(tmp_path))
    ready = work.update(sid, pid, ended["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"
    with work.store.transaction(True) as con:
        data = json.loads(con.execute("SELECT data FROM work_packages WHERE id=?", (pid,)).fetchone()[0])
        data["subtasks"][0]["review"]["agent_type"] = "scs-fixed-reviewer"
        con.execute("UPDATE work_packages SET data=? WHERE id=?", (encode(data), pid))
    with pytest.raises(ScsError) as mismatch:
        work.export(sid, pid, ready["version"])
    assert mismatch.value.code == "EVIDENCE_CHANGED"


async def test_zcode_long_review_text_fallback_uses_display_labels(zcode):
    async with Client(zcode, mode="legacy") as client:
        async def call(tool, **args):
            return (await client.call_tool(tool, args)).structured_content

        opened = await call("session_open", conversation_id="long-text", review_registration="registered")
        sid = opened["id"]
        for kind, choice in (("mode", "long"), ("verification", "targeted")):
            question = await call("interaction_request", session_id=sid, kind=kind,
                                  recommendation=[choice], channel="text")
            assert (await call("interaction_submit", session_id=sid, question_id=question["question_id"],
                               action="accept", choices=[choice], source="text"))["status"] == "CONFIRMED"

        question = await call("interaction_request", session_id=sid, kind="codex_gate",
                              recommendation=["packages"], channel="text")
        assert "长任务什么时候请固定审阅员审查？" in question["display_text"]
        assert [(option["label"], option["value"]) for option in question["options"]] == [
            ("仅收尾审查", "closure"), ("不做独立复核", "none"),
        ]
        for option in question["options"]:
            assert option["label"] in question["display_text"]
            assert option["value"] not in question["display_text"]
        assert (await call("interaction_submit", session_id=sid, question_id=question["question_id"],
                           action="accept", choices=["仅收尾审查"], source="text"))["status"] == "CONFIRMED"
        assert (await call("session_get", session_id=sid))["codex_gate"] == "closure"


async def test_manual_review_is_advisory_and_repeatable_by_user(zcode, tmp_path):
    async with Client(zcode, mode="legacy") as client:
        async def call(tool, **args):
            return (await client.call_tool(tool, args)).structured_content

        opened = await call("session_open", conversation_id="one-time", explicit_skip=True)
        sid = opened["id"]
        (tmp_path / "sample.txt").write_text("Sample evidence\n")
        definition = {"session_id": sid, "goal": "Check evidence", "allowed_files": ["sample.txt"],
                      "acceptance": ["Record findings"], "request_key": "one-time",
                      "subtasks": [{"id": "check", "kind": "analyze", "allowed_files": ["sample.txt"],
                                    "acceptance": ["Inspected"]}]}
        pkg = await call("work_package_create", **definition)
        pid = pkg["id"]
        state = zcode.state
        # Emulate an active package frozen under the old packages policy.
        with state.store.transaction(True) as con:
            data = json.loads(con.execute("SELECT data FROM work_packages WHERE id=?", (pid,)).fetchone()[0])
            data.update(review_required=True, native_review_required=True)
            con.execute("UPDATE work_packages SET data=? WHERE id=?", (json.dumps(data), pid))
        args = {"session_id": sid, "package_id": pid}
        assert not (await call("work_package_get", **args))["review_required"]
        rev = {**args, "expected_version": pkg["version"], "subtask_id": "review"}
        review = await call("work_attempt_prepare", **rev, user_requested=True)
        assert review["input_snapshot"]["user_requested"] and not review["review_required"]
        assert (await call("work_package_create", **definition))["id"] == pid
        final = write_evidence(tmp_path, review["evidence_dir"] + "/final.md", "结论: REJECT\n")
        final["path"] = "final.md"
        manifest = receipt("review_subject", review["review_subject"], final, verdict="REJECT")
        evidence = write_evidence(tmp_path, review["evidence_dir"] + "/manifest.json", json.dumps(manifest))
        ended = await call("work_attempt_update", session_id=sid, attempt_id=review["attempt"]["id"],
                           expected_version=review["version"], state="ENDED", host_agent_id="reviewer-1",
                           result={"status": "FAIL", "evidence": [evidence]})
        assert ended["task_status"] == "FAIL"
        assert not (await call("run_map", **args))["blockers"]
        progress = await call("work_package_update", **args, expected_version=ended["version"], state="IN_PROGRESS")
        ready = await call("work_package_update", **args, expected_version=progress["version"], state="REVIEW_READY")
        assert ready["state"] == "REVIEW_READY"
        revised = await call("work_package_update", **args, expected_version=ready["version"], new_revision=True)
        assert revised["revision"] == 2
        handoff = await call("handoff_export", **args, expected_version=revised["version"])
        other = await call("session_open", conversation_id="handoff", explicit_skip=True)
        await call("handoff_resume", session_id=other["id"], handoff_id=handoff["handoff_id"], sha256=handoff["sha256"])
        repeat = await call("work_attempt_prepare", session_id=other["id"], package_id=pid,
                            expected_version=revised["version"], subtask_id="review", user_requested=True)
        assert repeat["attempt"]["id"].startswith("A-")


async def test_main_questions_and_operation_gate_use_fixed_reviewer(zcode, tmp_path):
    async with Client(zcode, mode="legacy") as client:
        async def call(tool, **args):
            return (await client.call_tool(tool, args)).structured_content

        opened = await call("session_open", conversation_id="quick", explicit_skip=True,
                            review_registration="registered")
        assert opened["native_review"] and opened["mode"] == "quick"
        sid = opened["id"]
        question = await call("interaction_request", session_id=sid, kind="codex_review", recommendation=["yes"])
        assert question["status"] == "NATIVE_QUESTION_REQUIRED"
        assert "收尾要请固定审阅员" in question["display_text"]
        assert [(option["label"], option["value"]) for option in question["options"]] == [
            ("使用固定审阅员", "yes"), ("不使用", "no"),
        ]
        assert (await call("interaction_submit", session_id=sid, question_id=question["question_id"],
                           action="accept", choices=["yes"], source="native"))["status"] == "CONFIRMED"
        assert (await call("session_get", session_id=sid))["codex_review"] is True

        operation = {"session_id": sid, "operation_id": "op-1", "command": "remove generated outputs",
                     "cwd": str(tmp_path), "targets": ["generated"]}
        missing = await call("operation_gate_check", **operation)
        assert missing["status"] == "MISSING"
        report = write_evidence(tmp_path, "review.md", "Checked scope\n结论: ACCEPT\n")
        details = {**operation, "impact": "Generated outputs", "backup": "Source copy",
                   "rollback": "Rebuild generated files", "verdict": "ACCEPT", "user_confirmed": True}
        for role in ("scs-inherited-reviewer", LEGACY_REVIEWER):
            foreign = receipt("operation_digest", missing["operation_digest"], report, agent_type=role)
            assert (await call("operation_gate_record", **details, review_receipt=foreign))["error_code"] == "REVIEW_AGENT_MISMATCH"
        correct = receipt("operation_digest", missing["operation_digest"], report)
        assert (await call("operation_gate_record", **details, review_receipt=correct))["status"] == "OK"
        check = await call("operation_gate_check", **operation)
        assert check["status"] == "APPROVED" and check["user_confirmation_still_required"]


def test_persisted_legacy_gate_accept_can_be_checked_but_only_exactly_replayed(zcode, tmp_path):
    _, state, session = zcode.roots.open(str(tmp_path), "zcode", "legacy-gate", "unregistered", True)
    sid = session["id"]
    gate = Gate(state.store)
    operation = {"operation_id": "old-op", "command": "remove outputs", "cwd": str(tmp_path),
                 "targets": ["generated"], "baseline": {"head": "a123456"}}
    details = {"impact": "Generated outputs", "backup": "Source copy", "rollback": "Rebuild outputs",
               "verdict": "ACCEPT", "user_confirmed": True}
    digest = gate.check(sid, **operation)["operation_digest"]
    final = write_evidence(tmp_path, "old-review.md", "Checked scope\n结论: ACCEPT\n")
    legacy = receipt("operation_digest", digest, final, agent_type=LEGACY_REVIEWER)
    summary = {**operation, **details, "schema": 1, "operation_digest": digest, "digest": digest,
               "at": now(), "report_path": final["path"], "report_sha256": final["sha256"],
               "reason": None, "protocol_only": True, "review_receipt": legacy}
    with state.store.transaction(True) as con:
        state.store.event(con, sid, None, GATE_TOOL, encode(summary))
        seq = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    gate = Gate(Store(tmp_path))
    check = gate.check(sid, **operation)
    assert check["status"] == "APPROVED" and check["event_seq"] == seq
    assert gate.check(sid, **{**operation, "baseline": {"head": "b123456"}})["status"] == "STALE"
    (tmp_path / final["path"]).unlink()  # An exact replay returns the original event.
    replay = gate.record(sid, **operation, **details, review_receipt=legacy)
    assert replay["idempotent"] and replay["event_seq"] == seq and replay["review_receipt"] == legacy
    assert gate.check(sid, **operation)["status"] == "APPROVED"

    def rejected(code, changes=None, **receipt_changes):
        with pytest.raises(ScsError) as error:
            gate.record(sid, **{**operation, **details, **(changes or {})},
                        review_receipt={**legacy, **receipt_changes})
        assert error.value.code == code

    rejected("GATE_INVALID", {"user_confirmed": False})
    rejected("OPERATION_CONFLICT", {"command": "remove other outputs"})
    rejected("REVIEW_AGENT_MISMATCH", agent_id="reviewer-2")
    rejected("REVIEW_AGENT_MISMATCH", final_message={**final, "sha256": "f" * 64})
    rejected("REVIEW_AGENT_MISMATCH", {"verdict": "REJECT"}, verdict="REJECT")
    rejected("REVIEW_AGENT_MISMATCH", {"operation_id": "new-op"})
    assert gate.check(sid, **{**operation, "operation_id": "new-op"})["status"] == "MISSING"
    with gate.store.transaction() as con:
        assert con.execute("SELECT event FROM events WHERE tool=?", (GATE_TOOL,)).fetchone()[0] == encode(summary)
        assert con.execute("SELECT count(*) FROM events WHERE tool=?", (GATE_TOOL,)).fetchone()[0] == 1


def test_old_route_question_does_not_block_main_protocol(zcode, tmp_path):
    _, state, session = zcode.roots.open(str(tmp_path), "zcode", "old-native", "registered", False)
    sid = session["id"]
    with state.store.transaction(True) as con:
        con.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("old-route", sid, "review_route", 1, "WAITING_USER", "native", '["gpt"]', None, "old", "old"))
    assert state.get(sid)["unresolved"] == []
    assert state.request(sid, "mode", ["long"], "native")["kind"] == "mode"
    with pytest.raises(ScsError) as late:
        state.submit(sid, "old-route", "accept", ["gpt"], "native")
    assert late.value.code == "LATE_REPLY"
