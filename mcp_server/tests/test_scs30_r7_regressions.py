"""Regression tests for the scs30-r7 review finding fixed in R1 fix7.

A registered review result is only an approval when it is a PASS review or
when it explicitly recorded a subject digest.  Non-PASS results
(NOT_RUN/BLOCKED/SUBSTITUTED) with a manifest keep only their evidence
because ``_apply_result`` clears ``task["review"]``; bare manifest evidence
must not be mistaken for a stale approval and must not block REVIEW_READY or
export.  Genuine approvals stay strict: a fresh PASS+ACCEPT is accepted, an
expired PASS is still REVIEW_STALE, and ``review_required`` packages still
need a current PASS+ACCEPT (REVIEW_REQUIRED).
"""

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
    return store, state, Work(store)


def open_session(state, gate_policy, chat):
    """Open a long session and confirm the codex_gate policy under test."""
    sid = state.open(str(state.store.root), "test-host", chat)["id"]
    for kind, choice in (("mode", "long"), ("verification", "smoke")):
        question = state.request(sid, kind, [choice], "text")
        assert state.submit(sid, question["id"], "accept", [choice])["status"] == "CONFIRMED"
    question = state.request(sid, "codex_gate", [gate_policy], "text")
    assert state.submit(sid, question["id"], "accept", [gate_policy])["status"] == "CONFIRMED"
    return sid


def task_defs():
    return [
        {"id": "I1", "kind": "implement", "allowed_files": ["src/a.py"],
         "acceptance": ["implemented"]},
        {"id": REVIEW, "kind": "review", "allowed_files": ["src"],
         "acceptance": ["codex report"]},
    ]


def make(work, sid, key, review_required):
    package = work.create(sid, "review package", ["src"], ["accepted"],
                          task_defs(), key)
    assert package["review_required"] is review_required
    return work.update(sid, package["id"], package["version"], "IN_PROGRESS")


def write_manifest(store, package, attempt, subject, verdict="ACCEPT"):
    base = (f"{package['docs_root']}/agent/evidence/{package['id']}/"
            f"R{package['revision']}/{REVIEW}/{attempt['attempt']['id']}")
    raw_relative = f"{base}/manifest.final.md"
    (store.root / raw_relative).write_text(f"# Codex review\n\n结论: {verdict}\n",
                                           encoding="utf-8")
    manifest = {
        "verdict": verdict,
        "session_id": "codex-session-1",
        "model": "gpt-test",
        "subject_sha256": subject,
        "final_message_sha256": hashlib.sha256(
            (store.root / raw_relative).read_bytes()).hexdigest(),
        "command": "codex review --read-only",
        "exit_code": 0,
        "raw_path": "manifest.final.md",
    }
    relative = f"{base}/manifest.json"
    data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    (store.root / relative).write_bytes(data)
    return relative, hashlib.sha256(data).hexdigest()


def end_review(store, work, sid, package, attempt, verdict="ACCEPT",
               status="PASS"):
    path, sha = write_manifest(store, package, attempt,
                               attempt["review_subject"], verdict)
    return work.update_attempt(sid, attempt["attempt"]["id"], package["version"],
                               "ENDED", {"status": status, "evidence": [
                                   {"path": path, "sha256": sha}]})


def review_task(snapshot):
    return next(item for item in snapshot["subtasks"] if item["id"] == REVIEW)


def test_not_run_review_with_manifest_does_not_block_review_ready_or_export(env):
    """A NOT_RUN review keeps only evidence; it must not look like a stale approval."""
    store, state, work = env
    sid = open_session(state, "closure", "not-run-manifest")
    package = make(work, sid, "not-run-manifest", review_required=False)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt, "ACCEPT", status="NOT_RUN")
    assert ended["task_status"] == "NOT_RUN"

    refreshed = work.get(sid, package["id"])
    task = review_task(refreshed)
    assert task["status"] == "NOT_RUN"
    assert "review" not in task
    assert len(task["evidence"]) == 1

    ready = work.update(sid, package["id"], refreshed["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"
    assert review_task(ready)["status"] == "NOT_RUN"

    exported = work.export(sid, package["id"], ready["version"])
    assert exported["status"] == "EXPORTED"


def test_blocked_review_with_manifest_exports_without_review_stale(env):
    """A BLOCKED review manifest must not trip REVIEW_STALE on export.

    REVIEW_READY itself stays unavailable through the pre-existing
    ``RESULT_BLOCKED`` rule for FAIL/BLOCKED subtasks (out of scope here); the
    regression under test is that the failure is not ``REVIEW_STALE``.
    """
    store, state, work = env
    sid = open_session(state, "closure", "blocked-manifest")
    package = make(work, sid, "blocked-manifest", review_required=False)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt, "REJECT", status="BLOCKED")
    assert ended["task_status"] == "BLOCKED"

    refreshed = work.get(sid, package["id"])
    task = review_task(refreshed)
    assert task["status"] == "BLOCKED"
    assert "review" not in task
    assert len(task["evidence"]) == 1

    # BLOCKED is rejected for its own pre-existing reason, never as a stale review.
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], refreshed["version"], "REVIEW_READY")
    assert err.value.code == "RESULT_BLOCKED"
    assert err.value.code != "REVIEW_STALE"

    exported = work.export(sid, package["id"], refreshed["version"])
    assert exported["status"] == "EXPORTED"


@pytest.mark.parametrize("policy,review_required",
                         [("packages", True), ("closure", False)])
def test_fresh_pass_passes_review_ready_and_export(env, policy, review_required):
    """A legal fresh PASS+ACCEPT still opens REVIEW_READY and exports."""
    store, state, work = env
    sid = open_session(state, policy, "fresh-r7-" + policy)
    package = make(work, sid, "fresh-r7-" + policy,
                   review_required=review_required)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt)
    assert ended["task_status"] == "PASS"

    ready = work.update(sid, package["id"], ended["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"
    task = review_task(ready)
    assert task["status"] == "PASS"
    assert task["review"]["verdict"] == "ACCEPT"
    assert task["review"]["subject_sha256"] == attempt["review_subject"]

    exported = work.export(sid, package["id"], ready["version"])
    assert exported["status"] == "EXPORTED"


@pytest.mark.parametrize("policy,review_required",
                         [("closure", False), ("packages", True)])
def test_expired_pass_is_still_review_stale_at_export(env, policy, review_required):
    """A PASS that covered an older subject must not be exported as current."""
    store, state, work = env
    sid = open_session(state, policy, "expired-r7-" + policy)
    package = make(work, sid, "expired-r7-" + policy,
                   review_required=review_required)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt)
    ready = work.update(sid, package["id"], ended["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"

    # The reviewed snapshot changes after the ACCEPT was recorded.
    (store.root / "src" / "a.py").write_text("VALUE = 999\n", encoding="utf-8")

    with pytest.raises(ScsError) as err:
        work.export(sid, package["id"], ready["version"])
    assert err.value.code == "REVIEW_STALE"


def test_required_package_with_expired_pass_is_stale_at_review_ready(env):
    """The required gate keeps rejecting an ACCEPT that no longer covers the subject."""
    store, state, work = env
    sid = open_session(state, "packages", "required-expired-r7")
    package = make(work, sid, "required-expired-r7", review_required=True)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    end_review(store, work, sid, package, attempt)

    (store.root / "src" / "a.py").write_text("VALUE = 999\n", encoding="utf-8")
    refreshed = work.get(sid, package["id"])
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], refreshed["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_STALE"
    assert work.get(sid, package["id"])["state"] == "IN_PROGRESS"


def test_required_package_with_non_pass_review_is_review_required(env):
    """A manifest-carrying NOT_RUN review does not satisfy review_required."""
    store, state, work = env
    sid = open_session(state, "packages", "required-not-run-r7")
    package = make(work, sid, "required-not-run-r7", review_required=True)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt, "ACCEPT", status="NOT_RUN")
    assert ended["task_status"] == "NOT_RUN"

    refreshed = work.get(sid, package["id"])
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], refreshed["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_REQUIRED"
    assert work.get(sid, package["id"])["state"] == "IN_PROGRESS"


def test_required_package_without_review_is_review_required(env):
    """Existing behaviour: a required package still needs a PASS/ACCEPT review."""
    _, state, work = env
    sid = open_session(state, "packages", "required-missing-r7")
    package = make(work, sid, "required-missing-r7", review_required=True)

    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], package["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_REQUIRED"
    assert work.get(sid, package["id"])["state"] == "IN_PROGRESS"
