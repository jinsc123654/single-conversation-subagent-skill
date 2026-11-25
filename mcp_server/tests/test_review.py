"""Review subtask: strict manifest, subject freshness, and the review window."""

import hashlib
import json

import pytest

from scs_mcp.errors import ScsError
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work

REVIEW = "RV"


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path)
    state = State(store)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("OTHER = 2\n", encoding="utf-8")
    sid = state.open(str(tmp_path), "test", "long")["id"]
    for kind, choice in (("mode", ["long"]), ("verification", ["smoke"]),
                         ("codex_gate", ["packages"])):
        q = state.request(sid, kind, choice, "text")
        assert state.submit(sid, q["id"], "accept", choice)["status"] == "CONFIRMED"
    return store, state, sid, Work(store)


def make(work, sid, key="review", with_review=True):
    subtasks = [{"id": "I1", "kind": "implement", "allowed_files": ["src/a.py"],
                 "acceptance": ["implemented"]}]
    if with_review:
        subtasks.append({"id": REVIEW, "kind": "review", "allowed_files": ["src"],
                         "acceptance": ["codex report"]})
    package = work.create(sid, "review package", ["src"], ["accepted"], subtasks, key)
    return work.update(sid, package["id"], package["version"], "IN_PROGRESS")


def write_manifest(store, package, attempt, verdict="ACCEPT", raw=None,
                   overrides=None, drop=None, stem="manifest"):
    base = (f"{package['docs_root']}/agent/evidence/{package['id']}/"
            f"R{package['revision']}/{REVIEW}/{attempt['attempt']['id']}")
    raw = raw if raw is not None else f"# Codex review\n\n结论: {verdict}\n"
    raw_relative = f"{base}/{stem}.final.md"
    (store.root / raw_relative).write_text(raw, encoding="utf-8")
    manifest = {
        "verdict": verdict,
        "session_id": "codex-session-1",
        "model": "gpt-test",
        "subject_sha256": attempt["review_subject"],
        "final_message_sha256": hashlib.sha256((store.root / raw_relative).read_bytes()).hexdigest(),
        "command": "codex review --read-only",
        "exit_code": 0,
        "raw_path": f"{stem}.final.md",
    }
    if overrides:
        manifest.update(overrides)
    if drop:
        manifest.pop(drop, None)
    relative = f"{base}/{stem}.json"
    data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    (store.root / relative).write_bytes(data)
    return relative, hashlib.sha256(data).hexdigest()


def end_review(store, work, sid, package, attempt, verdict="ACCEPT", status="PASS", **kwargs):
    path, sha = write_manifest(store, package, attempt, verdict, **kwargs)
    return work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
        {"status": status, "evidence": [{"path": path, "sha256": sha}]})


def accept_review(store, work, sid, key="accepted"):
    package = make(work, sid, key)
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt, "ACCEPT", "PASS")
    return package, ended


def test_review_manifest_accept_and_reject_are_both_verified(env):
    store, _, sid, work = env
    package = make(work, sid, "both")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert attempt["review_required"] is True
    assert len(attempt["review_subject"]) == 64

    accept_path, _ = write_manifest(store, package, attempt, "ACCEPT")
    accepted = work.evidence(sid, package["id"], package["revision"], REVIEW, accept_path)
    assert accepted["status"] == "VERIFIED"
    assert accepted["review"]["verdict"] == "ACCEPT"

    reject_path, _ = write_manifest(store, package, attempt, "REJECT")
    rejected = work.evidence(sid, package["id"], package["revision"], REVIEW, reject_path)
    assert rejected["status"] == "VERIFIED"
    assert rejected["review"]["verdict"] == "REJECT"


def test_review_budget_limits_automatic_attempts(env):
    store, _, sid, work = env
    package = make(work, sid, "budget")
    for _ in range(2):
        current = work.get(sid, package["id"])
        attempt = work.prepare_attempt(sid, package["id"], current["version"], REVIEW)
        end_review(store, work, sid, current, attempt, "REJECT", "FAIL")
    current = work.get(sid, package["id"])
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, package["id"], current["version"], REVIEW)
    assert err.value.code == "REVIEW_BUDGET_EXHAUSTED"
    explicit = work.prepare_attempt(sid, package["id"], current["version"], REVIEW, user_requested=True)
    assert explicit["attempt"]["id"].startswith("A-")


@pytest.mark.parametrize("case,kwargs,expected", [
    ("missing_conclusion", {"raw": "# no conclusion\n"},
     "REVIEW_CONCLUSION_MISSING_OR_AMBIGUOUS"),
    ("ambiguous_conclusion", {"raw": "结论: ACCEPT\n结论: ACCEPT\n"},
     "REVIEW_CONCLUSION_MISSING_OR_AMBIGUOUS"),
    ("verdict_conflict", {"verdict": "ACCEPT", "raw": "结论: REJECT\n"},
     "REVIEW_VERDICT_CONFLICT"),
    ("hash_mismatch", {"overrides": {"final_message_sha256": "0" * 64}},
     "REVIEW_FINAL_MESSAGE_HASH_MISMATCH"),
    ("raw_traversal", {"overrides": {"raw_path": "../escape.md"}},
     "REVIEW_MANIFEST_INVALID"),
    ("raw_absolute", {"overrides": {"raw_path": "/etc/passwd"}},
     "REVIEW_MANIFEST_INVALID"),
    ("missing_field", {"drop": "model"}, "REVIEW_MANIFEST_INVALID"),
    ("extra_field", {"overrides": {"extra": True}}, "REVIEW_MANIFEST_INVALID"),
    ("bad_exit", {"overrides": {"exit_code": 1}},
     "REVIEW_EXIT_NONZERO_OR_UNKNOWN"),
    ("bool_exit", {"overrides": {"exit_code": False}},
     "REVIEW_EXIT_NONZERO_OR_UNKNOWN"),
    ("bad_verdict", {"verdict": "MAYBE", "raw": "结论: ACCEPT\n"},
     "REVIEW_VERDICT_INVALID"),
    ("bad_hash_shape", {"overrides": {"subject_sha256": "abc"}},
     "REVIEW_MANIFEST_INVALID"),
    ("missing_raw", {"overrides": {"raw_path": "absent.md"}}, "PATH_UNSAFE"),
])
def test_review_manifest_rejects_invalid_reports(env, case, kwargs, expected):
    store, _, sid, work = env
    package = make(work, sid, "invalid-" + case)
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    path, _ = write_manifest(store, package, attempt, **kwargs)
    checked = work.evidence(sid, package["id"], package["revision"], REVIEW, path)
    assert checked["status"] == "INVALID", (case, checked)
    assert checked["error_code"] == expected, (case, checked)


def test_review_manifest_must_be_a_json_object(env):
    store, _, sid, work = env
    package = make(work, sid, "not-json")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    path = attempt["evidence_dir"] + "/manifest.json"
    (store.root / path).write_text("not json", encoding="utf-8")
    checked = work.evidence(sid, package["id"], package["revision"], REVIEW, path)
    assert checked["status"] == "INSUFFICIENT"
    assert checked["error_code"] == "REVIEW_MANIFEST_REQUIRED"


def test_review_pass_requires_accept_and_fail_may_record_reject(env):
    store, _, sid, work = env
    package = make(work, sid, "pass-reject")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    path, sha = write_manifest(store, package, attempt, "REJECT")
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
            {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]})
    assert err.value.code == "REVIEW_VERDICT_INVALID"
    ended = work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
        {"status": "FAIL", "evidence": [{"path": path, "sha256": sha}]})
    assert ended["task_status"] == "FAIL"
    task = next(task for task in work.get(sid, package["id"])["subtasks"] if task["id"] == REVIEW)
    assert task["review"]["verdict"] == "REJECT"
    assert task["review"]["attempt_id"] == attempt["attempt"]["id"]

    other = make(work, sid, "fail-accept")
    other_attempt = work.prepare_attempt(sid, other["id"], other["version"], REVIEW)
    path, sha = write_manifest(store, other, other_attempt, "ACCEPT")
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, other_attempt["attempt"]["id"], other["version"], "ENDED",
            {"status": "FAIL", "evidence": [{"path": path, "sha256": sha}]})
    assert err.value.code == "REVIEW_VERDICT_INVALID"


def test_review_pass_needs_exactly_one_manifest(env):
    store, _, sid, work = env
    package = make(work, sid, "count")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
                            {"status": "PASS", "evidence": []})
    assert err.value.code == "EVIDENCE_REQUIRED"

    first, first_sha = write_manifest(store, package, attempt, "ACCEPT", stem="one")
    second, second_sha = write_manifest(store, package, attempt, "ACCEPT", stem="two")
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
            {"status": "PASS", "evidence": [
                {"path": first, "sha256": first_sha},
                {"path": second, "sha256": second_sha}]})
    assert err.value.code == "REVIEW_INVALID"
    assert work.list_attempts(sid, package["id"])["attempts"][0]["state"] != "ENDED"


def test_review_attempt_rejects_manifest_for_another_subject(env):
    store, _, sid, work = env
    package = make(work, sid, "stale-subject")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    path, sha = write_manifest(store, package, attempt, "ACCEPT",
                               overrides={"subject_sha256": "b" * 64})
    with pytest.raises(ScsError) as err:
        work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
            {"status": "PASS", "evidence": [{"path": path, "sha256": sha}]})
    assert err.value.code == "REVIEW_STALE"
    assert work.list_attempts(sid, package["id"])["attempts"][0]["state"] != "ENDED"


def test_review_forces_attempt_and_direct_results_are_rejected(env):
    _, _, sid, work = env
    package = make(work, sid, "direct")
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], package["version"],
                    results=[{"id": REVIEW, "status": "NOT_RUN"}])
    assert err.value.code == "REVIEW_ATTEMPT_REQUIRED"


def test_review_window_keeps_a_review_alone(env):
    _, _, sid, work = env
    package = make(work, sid, "window")
    first = work.prepare_attempt(sid, package["id"], package["version"], "I1")
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    assert err.value.code == "REVIEW_WINDOW_ACTIVE"
    ended = work.update_attempt(sid, first["attempt"]["id"], package["version"], "ENDED",
                                {"status": "NOT_RUN"})
    review = work.prepare_attempt(sid, package["id"], ended["version"], REVIEW)
    assert review["attempt"]["state"] == "PREPARED"
    with pytest.raises(ScsError) as err:
        work.prepare_attempt(sid, package["id"], ended["version"], "I1")
    assert err.value.code == "REVIEW_WINDOW_ACTIVE"


def test_batch_prepare_cannot_mix_review_with_other_subtasks(env):
    store, _, sid, work = env
    package = make(work, sid, "window-batch")
    with pytest.raises(ScsError) as err:
        work.prepare_attempts(sid, package["id"], package["version"], ["I1", REVIEW])
    assert err.value.code == "REVIEW_WINDOW_ACTIVE"
    with store.transaction() as con:
        assert con.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 0


def test_review_ready_requires_current_accept(env):
    store, _, sid, work = env
    package = make(work, sid, "gate")
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], package["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_REQUIRED"
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt, "ACCEPT", "PASS")
    ready = work.update(sid, package["id"], ended["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"
    completed = work.update(sid, package["id"], ready["version"], "COMPLETED",
                            close_reason="main conversation accepted")
    assert completed["state"] == "COMPLETED"


def test_changed_subject_blocks_review_ready(env):
    store, _, sid, work = env
    package, ended = accept_review(store, work, sid, "stale-ready")
    (store.root / "src/a.py").write_text("VALUE = 999\n", encoding="utf-8")
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], ended["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_STALE"


def test_changed_subject_blocks_completed_and_export(env):
    store, _, sid, work = env
    package, ended = accept_review(store, work, sid, "stale-completed")
    ready = work.update(sid, package["id"], ended["version"], "REVIEW_READY")
    (store.root / "src/a.py").write_text("VALUE = 1000\n", encoding="utf-8")
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], ready["version"], "COMPLETED",
                    close_reason="accepted")
    assert err.value.code == "REVIEW_STALE"
    with pytest.raises(ScsError) as err:
        work.export(sid, package["id"], ready["version"])
    assert err.value.code == "REVIEW_STALE"


def test_review_required_package_without_review_subtask_needs_revision(env):
    _, _, sid, work = env
    package = make(work, sid, "no-review", with_review=False)
    assert package["review_required"] is True
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], package["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_REQUIRED"


def test_legacy_package_without_review_required_defaults_false(env):
    store, _, sid, work = env
    package = make(work, sid, "legacy-package")
    with store.transaction(True) as con:
        row = con.execute("SELECT data FROM work_packages WHERE id=?", (package["id"],)).fetchone()
        data = json.loads(row["data"])
        data.pop("review_required", None)
        con.execute("UPDATE work_packages SET data=? WHERE id=?", (json.dumps(data), package["id"]))
    with store.transaction() as con:
        stored = json.loads(con.execute(
            "SELECT data FROM work_packages WHERE id=?", (package["id"],)).fetchone()["data"])
    assert "review_required" not in stored
    assert work.get(sid, package["id"])["review_required"] is False
    ready = work.update(sid, package["id"], package["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"
    revised = work.update(sid, package["id"], ready["version"], new_revision=True)
    assert revised["revision"] == 2 and revised["review_required"] is False


def test_update_many_accepts_a_single_review_result(env):
    store, _, sid, work = env
    package = make(work, sid, "update-many-review")
    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    path, sha = write_manifest(store, package, attempt, "REJECT")
    out = work.update_attempts(sid, [{
        "attempt_id": attempt["attempt"]["id"],
        "result": {"status": "FAIL", "evidence": [{"path": path, "sha256": sha}]},
    }], "review-batch")
    assert out["attempts"][0]["result"]["status"] == "FAIL"
    task = next(task for task in work.get(sid, package["id"])["subtasks"] if task["id"] == REVIEW)
    assert task["review"]["verdict"] == "REJECT"
