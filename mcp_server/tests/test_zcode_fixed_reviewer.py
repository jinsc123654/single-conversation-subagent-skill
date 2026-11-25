"""The main protocol uses registered execution agents and a fixed ZCode reviewer."""

import hashlib
import json

import pytest
from mcp import Client

from scs_mcp.errors import ScsError
from scs_mcp.server import create_server


@pytest.fixture
def zcode(tmp_path, monkeypatch):
    monkeypatch.setenv("SCS_NATIVE_REVIEW", "1")
    return create_server(tmp_path, question_channel="native", host="zcode")


def write_evidence(root, relative, content):
    path = root / relative
    path.write_text(content, encoding="utf-8")
    return {"path": relative, "sha256": hashlib.sha256(content.encode()).hexdigest()}


def receipt(key, digest, final, *, agent_type="固定审阅员", agent_id="reviewer-1", verdict="ACCEPT"):
    return {"schema_version": 2, key: digest, "agent_type": agent_type, "agent_id": agent_id,
            "model": "unobserved", "status": "completed", "verdict": verdict, "final_message": final}


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
    assert [item["agent_type"] for item in batch["attempts"]] == ["实施工程师", "实施工程师"]
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
    assert review["agent_type"] == review["input_snapshot"]["agent_type"] == "固定审阅员"
    aid = review["attempt"]["id"]
    final = write_evidence(tmp_path, review["evidence_dir"] + "/final-message.md", "Reviewed\n结论: ACCEPT\n")
    final["path"] = "final-message.md"
    manifest_path = review["evidence_dir"] + "/manifest.json"
    manifest = receipt("review_subject", review["review_subject"], final, agent_type="继承审阅员")

    def end(manifest, agent_id):
        evidence = write_evidence(tmp_path, manifest_path, json.dumps(manifest, ensure_ascii=False))
        return work.update_attempt(sid, aid, version, "ENDED", {"status": "PASS", "evidence": [evidence]},
                                   host_agent_id=agent_id)

    with pytest.raises(ScsError) as wrong_role:
        end(manifest, "reviewer-1")
    assert wrong_role.value.code == "REVIEW_AGENT_MISMATCH"
    manifest["agent_type"] = "固定审阅员"
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
        foreign = receipt("operation_digest", missing["operation_digest"], report, agent_type="继承审阅员")
        assert (await call("operation_gate_record", **details, review_receipt=foreign))["error_code"] == "REVIEW_AGENT_MISMATCH"
        correct = receipt("operation_digest", missing["operation_digest"], report)
        assert (await call("operation_gate_record", **details, review_receipt=correct))["status"] == "OK"
        check = await call("operation_gate_check", **operation)
        assert check["status"] == "APPROVED" and check["user_confirmation_still_required"]


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
