"""Regression tests for the scs30-r6 review finding fixed in R1 fix6.

The freshness of an already-registered review result must not depend on
``review_required``: a package that does not require a review still has to
report ``REVIEW_STALE`` when an old/expired PASS+ACCEPT is carried into
``REVIEW_READY`` (previously the check only ran for ``review_required=true``
and at COMPLETED/export). Packages with no review result at all keep the
previous ``REVIEW_READY`` behaviour, and required packages keep requiring a
fresh ACCEPT.
"""

import hashlib
import json

import pytest

import scs_mcp.work as work_module
from scs_mcp.errors import ScsError
from scs_mcp.state import State
from scs_mcp.storage import Store
from scs_mcp.work import Work

REVIEW = "RV"
REAL_SUBJECT_JSON = work_module._review_subject_json


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


def end_review(store, work, sid, package, attempt, subject, verdict="ACCEPT",
               status="PASS"):
    path, sha = write_manifest(store, package, attempt, subject, verdict)
    return work.update_attempt(sid, attempt["attempt"]["id"], package["version"], "ENDED",
        {"status": status, "evidence": [{"path": path, "sha256": sha}]})


def capture_subject(store, work, monkeypatch, package):
    """Return the direct subject digest and the canonical payload dict."""
    captured = []

    def spy(value):
        captured.append(value)
        return REAL_SUBJECT_JSON(value)

    monkeypatch.setattr(work_module, "_review_subject_json", spy)
    with store.transaction() as con:
        row = work._get(con, package["id"])
        digest = work._review_subject(row, json.loads(row["data"]))
    payload = next(item for item in captured
                   if isinstance(item, dict) and "files" in item)
    return digest, payload


def subject_digest(payload):
    """Digest a payload with the pre-fix5 canonical JSON (no version field)."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def store_old_accept(store, package, attempt, subject):
    """Persist an ACCEPT the way the pre-fix5 implementation would have.

    The package row is rewritten directly: the review subtask is PASS with a
    manifest bound to ``subject`` and the stored review record carries the same
    (unversioned) digest. This simulates an upgraded database whose package
    does not require a review.
    """
    path, sha = write_manifest(store, package, attempt, subject)
    with store.transaction(True) as con:
        row = con.execute("SELECT data FROM work_packages WHERE id=?",
                          (package["id"],)).fetchone()
        data = json.loads(row["data"])
        task = next(item for item in data["subtasks"] if item["id"] == REVIEW)
        task["status"] = "PASS"
        task["evidence"] = [{"path": path, "sha256": sha}]
        task["review"] = {"subject_sha256": subject, "verdict": "ACCEPT",
                          "attempt_id": attempt["attempt"]["id"]}
        con.execute("UPDATE work_packages SET data=? WHERE id=?",
                    (json.dumps(data), package["id"]))


def release_attempt(work, sid, package, attempt):
    """End a prepared attempt with NOT_RUN so the package is quiescent again."""
    return work.update_attempt(sid, attempt["attempt"]["id"], package["version"],
                               "ENDED", {"status": "NOT_RUN"})


def test_non_required_package_with_legacy_accept_is_stale_at_review_ready(env, monkeypatch):
    """P2: an unversioned old ACCEPT must not be reused by a non-required package."""
    store, state, work = env
    sid = open_session(state, "closure", "non-required-legacy")
    package = make(work, sid, "non-required-legacy", review_required=False)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    current, payload = capture_subject(store, work, monkeypatch, package)
    assert payload["subject_version"] == 2
    assert current == attempt["review_subject"]

    legacy_payload = dict(payload)
    legacy_payload.pop("subject_version")
    legacy = subject_digest(legacy_payload)
    assert legacy != current

    release_attempt(work, sid, package, attempt)
    store_old_accept(store, package, attempt, legacy)

    refreshed = work.get(sid, package["id"])
    review_task = next(item for item in refreshed["subtasks"] if item["id"] == REVIEW)
    assert review_task["status"] == "PASS"
    assert review_task["review"]["subject_sha256"] == legacy

    # REVIEW_READY, not only COMPLETED/export, must reject the stale ACCEPT.
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], refreshed["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_STALE"
    assert work.get(sid, package["id"])["state"] == "IN_PROGRESS"


def test_non_required_package_with_expired_accept_is_stale_at_review_ready(env):
    """A PASS whose subject changed after the review is stale at REVIEW_READY."""
    store, state, work = env
    sid = open_session(state, "closure", "non-required-expired")
    package = make(work, sid, "non-required-expired", review_required=False)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt, attempt["review_subject"])
    assert ended["task_status"] == "PASS"

    (store.root / "src" / "a.py").write_text("VALUE = 999\n", encoding="utf-8")
    refreshed = work.get(sid, package["id"])
    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], refreshed["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_STALE"
    assert work.get(sid, package["id"])["state"] == "IN_PROGRESS"


@pytest.mark.parametrize("policy", ["closure", "none"])
def test_non_required_package_without_review_passes_review_ready(env, policy):
    """No review result at all: REVIEW_READY stays a no-op for review checks."""
    _, state, work = env
    sid = open_session(state, policy, "non-required-clean-" + policy)
    package = make(work, sid, "non-required-clean-" + policy, review_required=False)

    ready = work.update(sid, package["id"], package["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"
    review_task = next(item for item in ready["subtasks"] if item["id"] == REVIEW)
    assert review_task["status"] == "NOT_RUN"
    assert review_task["evidence"] == []


def test_required_package_without_review_is_review_required(env):
    """Existing behaviour: a required package needs a PASS/ACCEPT."""
    _, state, work = env
    sid = open_session(state, "packages", "required-missing")
    package = make(work, sid, "required-missing", review_required=True)

    with pytest.raises(ScsError) as err:
        work.update(sid, package["id"], package["version"], "REVIEW_READY")
    assert err.value.code == "REVIEW_REQUIRED"
    assert work.get(sid, package["id"])["state"] == "IN_PROGRESS"


@pytest.mark.parametrize("policy,review_required",
                         [("packages", True), ("closure", False)])
def test_fresh_accept_passes_review_ready(env, policy, review_required):
    """Existing behaviour: a fresh PASS+ACCEPT still opens REVIEW_READY."""
    store, state, work = env
    sid = open_session(state, policy, "fresh-" + policy)
    package = make(work, sid, "fresh-" + policy, review_required=review_required)

    attempt = work.prepare_attempt(sid, package["id"], package["version"], REVIEW)
    ended = end_review(store, work, sid, package, attempt, attempt["review_subject"])
    assert ended["task_status"] == "PASS"

    ready = work.update(sid, package["id"], ended["version"], "REVIEW_READY")
    assert ready["state"] == "REVIEW_READY"
    review_task = next(item for item in ready["subtasks"] if item["id"] == REVIEW)
    assert review_task["status"] == "PASS"
    assert review_task["review"]["verdict"] == "ACCEPT"
    assert review_task["review"]["subject_sha256"] == attempt["review_subject"]
