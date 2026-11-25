import hashlib
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


@pytest.mark.parametrize("value,expected", [
    ({"kind": "build", "exit_code": 0, "command": "npm run build"}, "VERIFIED"),
    ({"kind": "lint", "exit_code": 0, "command": "eslint ."}, "VERIFIED"),
    ({"kind": "smoke", "exit_code": 0, "command": "node smoke.mjs"}, "INSUFFICIENT"),
    ({"kind": "smoke", "exit_code": 0, "command": "node smoke.mjs",
      "observations": "clicked copy; status shows copied"}, "VERIFIED"),
    ({"kind": "unknown", "exit_code": 0, "command": "x"}, "INVALID"),
    ({"kind": "build", "exit_code": 0, "command": "x",
      "artifacts": [{"path": "dist/a.js", "sha256": "a" * 64}]}, "VERIFIED"),
    ({"kind": "build", "exit_code": 0, "command": "x",
      "artifacts": [{"path": "../x", "sha256": "a" * 64}]}, "INVALID"),
    ({"kind": "build", "exit_code": 0, "command": "x",
      "source_sha256": {"src/a.py": "b" * 64}}, "VERIFIED"),
])
def test_evidence_kinds_and_provenance(setup, value, expected):
    _, _, sid, work = setup
    p = package(work, sid)
    path = evidence_file(setup, p, value)
    check = work.evidence(sid, p["id"], 1, "T1", path)
    assert check["status"] == expected


def confirm_intake(state, sid, verification="journey", gate=True):
    answers = [("mode", "long"), ("verification", verification)]
    if gate:
        answers.append(("codex_gate", "none"))
    for kind, answer in answers:
        question = state.request(sid, kind, [answer], "text")
        assert state.submit(sid, question["id"], "accept", [answer], "text")["status"] == "CONFIRMED"


def journey_package(work, sid, resources=None):
    if resources is None:
        resources = [{"resource": "device:board", "mode": "write"}]
    return work.create(sid, "Observed journey", ["src"], ["observed"], [
        {"id": "T1", "kind": "test", "execution_profile": "journey",
         "allowed_files": ["src/a.py"], "acceptance": ["observed"], "resources": resources},
        {"id": "T2", "kind": "test", "allowed_files": ["src/a.py"], "acceptance": ["tests"]},
    ], "journey")


@pytest.fixture
def journey(setup):
    store, state, _, work = setup
    sid = state.open(str(store.root), "test", "journey")["id"]
    confirm_intake(state, sid)
    p = journey_package(work, sid)
    return store, sid, work, p


def write_journey(store, base, *, ok=True, exit_code=0, nested=False):
    image = b"recorded screenshot"
    (store.root / base).mkdir(parents=True, exist_ok=True)
    (store.root / base / "screen.png").write_bytes(image)
    manifest = {"kind": "journey", "channel": "adb", "exit_code": exit_code,
                "command": "adb shell input tap 1 2",
                "assertions": [{"name": "device visible", "ok": ok}],
                "artifacts": [{"path": "screen.png", "sha256": hashlib.sha256(image).hexdigest()}]}
    directory = base + "/reports" if nested else base
    (store.root / directory).mkdir(exist_ok=True)
    path = f"{directory}/result.json"
    content = json.dumps(manifest).encode()
    (store.root / path).write_bytes(content)
    return {"path": path, "sha256": hashlib.sha256(content).hexdigest()}


def test_journey_cannot_be_registered_or_prepared_as_standard(journey):
    store, sid, work, p = journey
    pid, version = p["id"], p["version"]
    assert p["subtasks"][0]["execution_profile"] == "journey"
    assert journey_package(work, sid)["id"] == pid
    for subtask, profile in (("T1", "standard"), ("T2", "journey")):
        with pytest.raises(ScsError) as exc:
            work.prepare_attempt(sid, pid, version, subtask, execution_profile=profile)
        assert exc.value.code == "INPUT_INVALID"
    for selected in (["T1"], ["T2", "T1"]):
        with pytest.raises(ScsError) as exc:
            work.prepare_attempts(sid, pid, version, selected)
        assert exc.value.code == "JOURNEY_SINGLE_REQUIRED"
    assert work.list_attempts(sid, pid)["attempts"] == []

    build = b'{"kind":"build","command":"build","exit_code":0}'
    build_path = f"{p['docs_root']}/agent/evidence/{pid}/R1/T1/build.json"
    (store.root / build_path).write_bytes(build)
    evidence = {"path": build_path, "sha256": hashlib.sha256(build).hexdigest()}
    for status in ("PASS", "FAIL", "BLOCKED", "NOT_RUN", "SUBSTITUTED"):
        with pytest.raises(ScsError) as exc:
            work.update(sid, pid, version, results=[{"id": "T1", "status": status, "evidence": [evidence]}])
        assert exc.value.code == "JOURNEY_ATTEMPT_REQUIRED"

    prepared = work.prepare_attempt(sid, pid, version, "T1")
    assert prepared["input_snapshot"]["execution_profile"] == "journey"
    evidence["path"] = prepared["evidence_dir"] + "/build.json"
    (store.root / evidence["path"]).write_bytes(build)
    with pytest.raises(ScsError) as exc:
        work.update_attempt(sid, prepared["attempt"]["id"], version, "ENDED", {"status": "PASS", "evidence": [evidence]})
    assert exc.value.code == "JOURNEY_NOT_PASSED"


@pytest.mark.parametrize("resources", [
    [],
    [{"resource": "serial:board", "mode": "write"}],
    [{"resource": "device:board", "mode": "read"}],
    [{"resource": "device:board"}, {"resource": "device:phone"}],
    [{"resource": "device:board"}, {"resource": "serial:board", "mode": "read"}],
    [{"resource": "device:board"}, {"resource": "programmer:phone"}],
])
def test_journey_requires_one_exclusive_physical_device(setup, resources):
    store, state, _, work = setup
    sid = state.open(str(store.root), "test", "journey")["id"]
    confirm_intake(state, sid)
    p = journey_package(work, sid, resources)
    with pytest.raises(ScsError) as exc:
        work.prepare_attempt(sid, p["id"], p["version"], "T1")
    assert exc.value.code == "JOURNEY_RESOURCE_REQUIRED"
    assert work.list_attempts(sid, p["id"])["attempts"] == []


def test_only_test_subtasks_can_declare_journey(setup):
    _, _, sid, work = setup
    with pytest.raises(ScsError) as exc:
        work.create(sid, "Wrong profile", ["src"], ["observed"], [
            {"id": "T1", "kind": "implement", "execution_profile": "journey",
             "allowed_files": ["src"], "acceptance": ["observed"]}], "wrong-profile")
    assert exc.value.code == "SUBTASK_INVALID"


def test_journey_handoff_requires_intake_and_verification_scope(journey):
    store, sid, work, p = journey
    pid = p["id"]
    with pytest.raises(ScsError) as exc:
        work.export(sid, pid, p["version"])
    assert exc.value.code == "JOURNEY_NOT_PASSED"
    suspended = work.update(sid, pid, p["version"], "SUSPENDED", suspension_reason="waiting for device")
    handoff = work.export(sid, pid, suspended["version"])
    version = suspended["version"]
    other = work.state.open(str(store.root), "test", "resumed")["id"]
    work.resume(other, handoff["handoff_id"], handoff["sha256"])
    confirm_intake(work.state, other, gate=False)
    version = work.update(sid, pid, version, "IN_PROGRESS")["version"]
    with pytest.raises(ScsError) as exc:
        work.prepare_attempt(other, pid, version, "T1")
    assert exc.value.code == "INTAKE_REQUIRED"

    third = work.state.open(str(store.root), "test", "targeted")["id"]
    confirm_intake(work.state, third, verification="targeted")
    p = journey_package(work, third)
    with pytest.raises(ScsError) as exc:
        work.prepare_attempt(third, p["id"], p["version"], "T1")
    assert exc.value.code == "VALIDATION_NOT_AUTHORIZED"


@pytest.mark.parametrize("ok,exit_code,batch", [(False, 0, False), (True, 1, True)])
def test_journey_rejects_failed_pass_but_archives_fail(journey, ok, exit_code, batch):
    store, sid, work, p = journey
    prepared = work.prepare_attempt(sid, p["id"], p["version"], "T1")
    evidence = write_journey(store, prepared["evidence_dir"], ok=ok, exit_code=exit_code)
    check = work.evidence(sid, p["id"], 1, "T1", evidence["path"])
    assert check["status"] == "VERIFIED" and check["journey_passed"] == ok
    aid = prepared["attempt"]["id"]

    def end(status, operation):
        result = {"status": status, "evidence": [evidence]}
        if batch:
            return work.update_attempts(sid, [{"attempt_id": aid, "result": result}], operation)
        return work.update_attempt(sid, aid, p["version"], "ENDED", result)

    with pytest.raises(ScsError) as exc:
        end("PASS", "bad-pass")
    assert exc.value.code == "JOURNEY_NOT_PASSED"
    version = end("FAIL", "failed-run")["version"]
    failed = work.get(sid, p["id"])["subtasks"][0]
    assert failed["status"] == "FAIL" and failed["result_attempt_id"] == aid
    suspended = work.update(sid, p["id"], version, "SUSPENDED", suspension_reason="retry later")
    assert work.export(sid, p["id"], suspended["version"])["snapshot"]["subtasks"][0]["status"] == "FAIL"


def test_journey_requires_pass_for_acceptance_and_rechecks_attachment(journey):
    store, sid, work, p = journey
    pid = p["id"]
    version = work.update(sid, pid, p["version"], "IN_PROGRESS")["version"]
    with pytest.raises(ScsError) as exc:
        work.force_close(pid, version, "COMPLETED", "missing result")
    assert exc.value.code == "JOURNEY_NOT_PASSED"
    with pytest.raises(ScsError) as exc:
        work.update(sid, pid, version, "REVIEW_READY")
    assert exc.value.code == "JOURNEY_NOT_PASSED"
    blocked = work.prepare_attempt(sid, pid, version, "T1")
    version = work.update_attempt(sid, blocked["attempt"]["id"], version, "ENDED",
                                  {"status": "SUBSTITUTED"})["version"]
    with pytest.raises(ScsError) as exc:
        work.update(sid, pid, version, "REVIEW_READY")
    assert exc.value.code == "JOURNEY_NOT_PASSED"
    prepared = work.prepare_attempt(sid, pid, version, "T1")
    evidence = write_journey(store, prepared["evidence_dir"], nested=True)
    check = work.evidence(sid, pid, 1, "T1", evidence["path"])
    assert check["status"] == "VERIFIED" and check["journey_passed"] is True
    assert check["journey_attempt_id"] == prepared["attempt"]["id"]
    result = work.update_attempt(sid, prepared["attempt"]["id"], version, "ENDED",
                                 {"status": "PASS", "evidence": [evidence]})
    ready = work.update(sid, pid, result["version"], "REVIEW_READY")
    handoff = work.export(sid, pid, ready["version"])
    other = work.state.open(str(store.root), "test", "handoff")["id"]
    assert work.resume(other, handoff["handoff_id"], handoff["sha256"])["status"] == "RESUMED"
    attachment = store.root / prepared["evidence_dir"] / "screen.png"
    original = attachment.read_bytes()
    attachment.write_bytes(b"replaced")
    check = work.evidence(sid, pid, 1, "T1", evidence["path"])
    assert check["error_code"] == "JOURNEY_ARTIFACT_INVALID"
    for verify in (lambda: work.export(sid, pid, ready["version"]),
                   lambda: work.resume(other, handoff["handoff_id"], handoff["sha256"]),
                   lambda: work.update(sid, pid, ready["version"], "COMPLETED", close_reason="accepted")):
        with pytest.raises(ScsError) as exc:
            verify()
        assert exc.value.code == "EVIDENCE_CHANGED"
    attachment.write_bytes(original)
    assert work.update(sid, pid, ready["version"], "COMPLETED", close_reason="accepted")["state"] == "COMPLETED"


def test_journey_evidence_requires_a_real_attempt_for_its_subtask(journey):
    store, sid, work, p = journey
    pid, version = p["id"], p["version"]
    root = f"{p['docs_root']}/agent/evidence/{pid}/R1"
    real = work.prepare_attempt(sid, pid, version, "T1")
    normal = work.prepare_attempt(sid, pid, version, "T2")
    candidates = [("T1", root + "/T1"), ("T1", root + "/T1/fake"),
                  ("T2", root + "/T2/" + real["attempt"]["id"]), ("T2", normal["evidence_dir"])]
    for subtask, base in candidates:
        proof = write_journey(store, base)
        assert work.evidence(sid, pid, 1, subtask, proof["path"])["error_code"] == "JOURNEY_ATTEMPT_REQUIRED"
    with pytest.raises(ScsError) as exc:
        work.update_attempt(sid, normal["attempt"]["id"], version, "ENDED",
                            {"status": "PASS", "evidence": [proof]})
    assert exc.value.code == "JOURNEY_ATTEMPT_REQUIRED"


@pytest.mark.parametrize("status", ["PASS", "FAIL"])
def test_unbound_journey_direct_and_stored_results_are_rejected(setup, status):
    store, _, sid, work = setup
    p = package(work, sid)
    pid = p["id"]
    proof = write_journey(store, f"{p['docs_root']}/agent/evidence/{pid}/R1/T1")
    report = {"id": "T1", "status": status, "evidence": [proof]}
    with pytest.raises(ScsError) as exc:
        work.update(sid, pid, p["version"], results=[report])
    assert exc.value.code == "JOURNEY_ATTEMPT_REQUIRED"
    # Simulate a manifest accepted by the older direct-reporting path.
    with store.transaction(True) as con:
        data = json.loads(work._get(con, pid)["data"])
        data["subtasks"][0].update(status=status, evidence=[proof])
        con.execute("UPDATE work_packages SET data=? WHERE id=?", (json.dumps(data), pid))
    with pytest.raises(ScsError) as exc:
        work.export(sid, pid, p["version"])
    assert exc.value.code == "JOURNEY_ATTEMPT_REQUIRED"


def test_legacy_journey_attempt_history_prevents_standard_results(journey):
    store, sid, work, p = journey
    pid = p["id"]
    run = work.prepare_attempt(sid, pid, p["version"], "T1")
    version = work.update_attempt(sid, run["attempt"]["id"], p["version"], "ENDED",
                                  {"status": "FAIL"})["version"]
    with store.transaction(True) as con:
        data = json.loads(work._get(con, pid)["data"])
        task = data["subtasks"][0]
        # Simulate a legacy failure without evidence or task-level provenance.
        task.pop("execution_profile")
        task.pop("result_attempt_id")
        con.execute("UPDATE work_packages SET data=? WHERE id=?", (json.dumps(data), pid))

    content = b'{"kind":"build","command":"build","exit_code":0}'
    path = f"{p['docs_root']}/agent/evidence/{pid}/R1/T1/build.json"
    (store.root / path).write_bytes(content)
    build = {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
    with pytest.raises(ScsError) as exc:
        work.update(sid, pid, version, results=[{"id": "T1", "status": "PASS", "evidence": [build]}])
    assert exc.value.code == "JOURNEY_ATTEMPT_REQUIRED"
    with pytest.raises(ScsError) as exc:
        work.prepare_attempts(sid, pid, version, ["T1"])
    assert exc.value.code == "JOURNEY_SINGLE_REQUIRED"
    with pytest.raises(ScsError) as exc:
        work.prepare_attempt(sid, pid, version, "T1", execution_profile="standard")
    assert exc.value.code == "INPUT_INVALID"

    retry = work.prepare_attempt(sid, pid, version, "T1")
    assert retry["input_snapshot"]["execution_profile"] == "journey"
    build["path"] = retry["evidence_dir"] + "/build.json"
    (store.root / build["path"]).write_bytes(content)
    with pytest.raises(ScsError) as exc:
        work.update_attempt(sid, retry["attempt"]["id"], version, "ENDED",
                            {"status": "PASS", "evidence": [build]})
    assert exc.value.code == "JOURNEY_NOT_PASSED"
    proof = write_journey(store, retry["evidence_dir"])
    ended = work.update_attempt(sid, retry["attempt"]["id"], version, "ENDED",
                                {"status": "PASS", "evidence": [proof]})
    assert work.export(sid, pid, ended["version"])["status"] == "EXPORTED"
    revised = work.update(sid, pid, ended["version"], new_revision=True)
    assert revised["subtasks"][0]["execution_profile"] == "journey"
    assert revised["subtasks"][0]["status"] == "NOT_RUN"


@pytest.mark.parametrize("batch", [False, True])
def test_damaged_history_does_not_trap_another_attempts_lease(setup, batch):
    store, state, _, work = setup
    sid = state.open(str(store.root), "test", "two-device-runs")["id"]
    confirm_intake(state, sid)
    p = work.create(sid, "Two journeys", ["src"], ["observed"], [
        {"id": key, "kind": "test", "execution_profile": "journey", "allowed_files": ["src"],
         "acceptance": ["observed"], "resources": [{"resource": "device:board", "mode": "write"}]}
        for key in ("T1", "T2")
    ], "two-journeys")
    pid = p["id"]
    version = work.update(sid, pid, p["version"], "IN_PROGRESS")["version"]
    first = work.prepare_attempt(sid, pid, version, "T1")
    proof = write_journey(store, first["evidence_dir"], ok=False)
    version = work.update_attempt(sid, first["attempt"]["id"], version, "ENDED",
                                  {"status": "FAIL", "evidence": [proof]})["version"]
    second = work.prepare_attempt(sid, pid, version, "T2")
    (store.root / first["evidence_dir"] / "screen.png").unlink()
    aid = second["attempt"]["id"]
    if batch:
        ended = work.update_attempts(sid, [{"attempt_id": aid, "result": {"status": "BLOCKED"}}],
                                     "stop-after-attachment-loss")
    else:
        ended = work.update_attempt(sid, aid, version, "ENDED", {"status": "BLOCKED"})
    with pytest.raises(ScsError) as exc:
        work.export(sid, pid, ended["version"])
    assert exc.value.code == "EVIDENCE_CHANGED"
    retry = work.prepare_attempt(sid, pid, ended["version"], "T1")
    assert retry["leases"][0]["resource"] == "device:board"


def test_stored_journey_uses_the_evidence_recorded_on_the_attempt(journey):
    store, sid, work, p = journey
    pid = p["id"]
    run = work.prepare_attempt(sid, pid, p["version"], "T1")
    proof = write_journey(store, run["evidence_dir"])
    result = work.update_attempt(sid, run["attempt"]["id"], p["version"], "ENDED",
                                 {"status": "PASS", "evidence": [proof]})
    duplicate = {**proof, "path": run["evidence_dir"] + "/copy.json"}
    (store.root / duplicate["path"]).write_bytes((store.root / proof["path"]).read_bytes())
    assert work.evidence(sid, pid, 1, "T1", duplicate["path"])["status"] == "VERIFIED"
    with store.transaction(True) as con:
        data = json.loads(work._get(con, pid)["data"])
        data["subtasks"][0]["evidence"] = [duplicate]
        con.execute("UPDATE work_packages SET data=? WHERE id=?", (json.dumps(data), pid))
    with pytest.raises(ScsError) as exc:
        work.export(sid, pid, result["version"])
    assert exc.value.code == "JOURNEY_ATTEMPT_REQUIRED"


@pytest.mark.parametrize("case,code", [
    ("no_assertions", "JOURNEY_OBSERVATION_MISSING"),
    ("no_artifacts", "JOURNEY_ARTIFACT_REQUIRED"),
    ("missing", "JOURNEY_ARTIFACT_INVALID"),
    ("symlink", "JOURNEY_ARTIFACT_INVALID"),
    ("escape", "EVIDENCE_KIND_INVALID"),
])
def test_journey_rejects_invalid_artifacts_and_assertions(journey, case, code):
    store, sid, work, p = journey
    run = work.prepare_attempt(sid, p["id"], p["version"], "T1")
    proof = write_journey(store, run["evidence_dir"])
    manifest_path = store.root / proof["path"]
    manifest = json.loads(manifest_path.read_text())
    if case == "no_assertions":
        manifest["assertions"] = []
    elif case == "no_artifacts":
        manifest["artifacts"] = []
    elif case == "missing":
        manifest["artifacts"][0]["path"] = "missing.png"
    elif case == "escape":
        manifest["artifacts"][0]["path"] = "../screen.png"
    else:
        image = manifest_path.with_name("screen.png")
        target = manifest_path.with_name("actual.png")
        image.rename(target)
        image.symlink_to(target.name)
    manifest_path.write_text(json.dumps(manifest))
    assert work.evidence(sid, p["id"], 1, "T1", proof["path"])["error_code"] == code


def test_suspend_resume_and_block_new_attempts(setup):
    _, _, sid, work = setup
    p = package(work, sid)
    suspended = work.update(sid, p["id"], p["version"], "SUSPENDED",
                            suspension_reason="user paused",
                            resume_condition="user confirms",
                            known_limitations=["edge inputs deferred"])
    assert suspended["state"] == "SUSPENDED"
    assert suspended["suspension"]["reason"] == "user paused"
    assert suspended["suspension"]["known_limitations"] == ["edge inputs deferred"]
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, p["id"], suspended["version"], "T1")
    assert err.value.code == "PACKAGE_SUSPENDED"
    resumed = work.update(sid, p["id"], suspended["version"], "IN_PROGRESS")
    assert resumed["state"] == "IN_PROGRESS"
    assert "suspension" not in resumed
    assert resumed["suspension_history"][0]["reason"] == "user paused"


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


def test_force_close_orphan_package_bypasses_state_machine(setup):
    store, _, sid, work = setup
    p = package(work, sid)
    result = work.force_close(p["id"], p["version"], "COMPLETED", "orphan completed elsewhere")
    assert result["state"] == "COMPLETED"
    assert result["forced"] is True
    assert result["close_reason"] == "orphan completed elsewhere"
    assert result["version"] == p["version"] + 1
    again = work.force_close(p["id"], None, "COMPLETED", "again")
    assert again["state"] == "COMPLETED"
    with pytest.raises(ScsError):
        work.force_close(p["id"], None, "ABANDONED", "")
    with pytest.raises(ScsError):
        work.force_close(p["id"], None, "IN_PROGRESS", "bad target")


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


@pytest.mark.parametrize("first_mode,second_mode", [
    ("read", "read"), ("read", "write"), ("write", "read"), ("write", "write"),
])
def test_device_aliases_conflict_in_every_mode(setup, first_mode, second_mode):
    store, _, sid, work = setup

    def create(resource, mode, key):
        return work.create(sid, "Device operation", ["src"], ["observed"], [
            {"id": "T1", "kind": "test", "allowed_files": ["src"], "acceptance": ["observed"],
             "resources": [{"resource": resource, "mode": mode}]},
        ], key)

    first = create("serial:board", first_mode, "serial")
    second = create("programmer:board", second_mode, "programmer")
    run = work.prepare_attempt(sid, first["id"], first["version"], "T1")
    work.leases.acquire(sid, "serial:board", lease_token=run["leases"][0]["lease_token"])
    with store.transaction() as con:
        assert con.execute("SELECT mode FROM leases WHERE resource='serial:board'").fetchone()["mode"] == first_mode
    with pytest.raises(ScsError) as exc:
        work.prepare_attempt(sid, second["id"], second["version"], "T1")
    assert exc.value.code == "RESOURCE_BUSY"
    assert work.list_attempts(sid, second["id"])["attempts"] == []
    work.prepare_attempt(sid, create("device:other", second_mode, "other")["id"], 1, "T1")


def test_renewing_attempt_lease_keeps_exclusivity_after_ttl(setup):
    store, state, _, work = setup
    sid = state.open(str(store.root), "test", "journey")["id"]
    confirm_intake(state, sid)
    resources = [{"resource": f"{prefix}:board", "mode": "write"}
                 for prefix in ("device", "serial", "programmer")]
    p = journey_package(work, sid, resources)
    clock = [10]
    work.leases.clock = lambda: clock[0]
    run = work.prepare_attempt(sid, p["id"], p["version"], "T1", ttl_seconds=1)
    token = next(lease["lease_token"] for lease in run["leases"] if lease["resource"] == "device:board")
    aid = run["attempt"]["id"]
    clock[0] = 12
    renewed = work.leases.acquire(sid, "device:board", 1, token)
    assert renewed["lease_token"] == token and renewed["expires_at"] == 13
    with store.transaction() as con:
        row = con.execute("SELECT * FROM leases WHERE resource='device:board'").fetchone()
        assert (row["attempt_id"], row["package_id"], row["mode"]) == (aid, p["id"], "write")
    with pytest.raises(ScsError) as exc:
        work.leases.release(sid, "device:board", token)
    assert exc.value.code == "LEASE_ACTIVE_ATTEMPT"
    clock[0] = 14
    other = state.open(str(store.root), "test", "other-device-user", explicit_skip=True)["id"]
    assert work.leases.acquire(other, "device:board")["status"] == "CONFLICT"
    assert work.leases.acquire(other, "serial:board")["status"] == "CONFLICT"
    work.update_attempt(sid, aid, p["version"], "ENDED", {"status": "BLOCKED"})
    assert work.leases.release(sid, "device:board", token)["status"] == "ALREADY_RELEASED"
    assert work.leases.acquire(other, "device:board")["status"] == "ACQUIRED"
    with store.transaction() as con:
        row = con.execute("SELECT * FROM leases WHERE resource='device:board'").fetchone()
        assert (row["attempt_id"], row["package_id"], row["mode"]) == (None, None, "write")
    with pytest.raises(ScsError) as exc:
        work.leases.acquire(sid, "serial:board")
    assert exc.value.code == "RESOURCE_BUSY"


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


def test_abandoned_closes_pending_package_and_keeps_reason(setup):
    store, _, sid, work = setup
    p = package(work, sid)
    closed = work.update(sid, p["id"], p["version"], "ABANDONED", close_reason="superseded by WP-108")
    assert closed["state"] == "ABANDONED"
    assert closed["close_reason"] == "superseded by WP-108"
    assert closed["closed_by_session"] == sid
    assert closed["closed_at"] == closed["updated_at"]
    with sqlite3.connect(store.root / ".scs/state.sqlite3") as con:
        assert con.execute("SELECT tool,event FROM events ORDER BY seq DESC LIMIT 1").fetchone() == (
            "work_package_update", "PACKAGE_CLOSED")
    with pytest.raises(ScsError) as err:
        work.update(sid, p["id"], closed["version"], "IN_PROGRESS")
    assert err.value.code == "PACKAGE_CLOSED"


def test_completed_requires_review_ready_and_reason(setup):
    _, _, sid, work = setup
    p = package(work, sid)
    with pytest.raises(ScsError) as err:
        work.update(sid, p["id"], p["version"], "COMPLETED", close_reason="accepted")
    assert err.value.code == "STATE_INVALID"
    p = work.update(sid, p["id"], p["version"], "IN_PROGRESS")
    p = work.update(sid, p["id"], p["version"], "REVIEW_READY")
    with pytest.raises(ScsError) as err:
        work.update(sid, p["id"], p["version"], "COMPLETED")
    assert err.value.code == "CLOSE_REASON_REQUIRED"
    done = work.update(sid, p["id"], p["version"], "COMPLETED", close_reason="main conversation accepted")
    assert done["state"] == "COMPLETED" and done["close_reason"] == "main conversation accepted"


def test_close_reason_rejected_without_terminal_state(setup):
    _, _, sid, work = setup
    p = package(work, sid)
    with pytest.raises(ScsError) as err:
        work.update(sid, p["id"], p["version"], "IN_PROGRESS", close_reason="not closed")
    assert err.value.code == "INPUT_INVALID"
